"""链上地址监控(不占 Dune 额度):Etherscan / Tronscan / mempool.space → 飞书。

默认监控 Ledger/CryptoBilis 相关 watchlist。新交易且满足以下任一条件则告警:
  - 原生币或主流稳定币金额 >= --min-native / --min-stable
  - 对手方命中 counterparties.json(Tornado Cash / Binance 等)

也可 --watchlist 指定美国政府 Bitfinex 没收资金等名单。

Usage:
  python monitor.py                 # 拉增量、写 state,不推飞书
  python monitor.py --push          # 有新告警才推 LARK_ALERT_WEBHOOK
  python monitor.py --dry-run       # 只打印将检查的地址,不请求链上
  python monitor.py --bootstrap     # 只记当前最新 hash,不告警(首次上线用)
  python monitor.py --watchlist watchlist_usg_bitfinex.json --history-alert --push
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = HERE / "output"
STATE_PATH = OUT_DIR / "state.json"
EVENTS_PATH = OUT_DIR / "events.json"
WATCHLIST_PATH = HERE / "watchlist.json"
COUNTERPARTIES_PATH = HERE / "counterparties.json"

HTTP_TIMEOUT = 45.0
USER_AGENT = "wallet-watch/1.0 (+data-download)"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
# 无 ETHERSCAN_API_KEY 时用 Blockscout 兼容接口(免费、无需 key)
BLOCKSCOUT_API = "https://eth.blockscout.com/api"
TRONSCAN_API = "https://apilist.tronscanapi.com/api"
MEMPOOL_API = "https://mempool.space/api"

TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/wallet-watch"
ARKHAM_USG1 = "https://arkm.com/explorer/entity/usg1"
FEED_PATHS = [
    OUT_DIR / "latest.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "wallet-watch" / "latest.json",
    Path("/root/dashboard/public/json/wallet-watch/latest.json"),
]

# ERC-20 / TRC-20 稳定币合约 → 符号(金额按 decimals 换算后与 --min-stable 比)
ETH_STABLE = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0x4fabb145d64652a948d72533023f6e7a623c7c53": ("BUSD", 18),
}
TRON_STABLE = {
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": ("USDT", 6),
}


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    # data-download/.env 优先；飞书 webhook 常在 dashboard/.env.live
    env: dict[str, str] = {}
    for path in (
        Path("/root/dashboard/.env.live"),
        ROOT.parent / "data-dashboard" / ".env.live",
        ROOT.parent / "dashboard" / ".env.live",
        ROOT / ".env",
    ):
        env.update(load_env(path))
    for k in (
        "ETHERSCAN_API_KEY",
        "LARK_ALERT_WEBHOOK",
        "LARK_ALERT_SECRET",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
    ):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def http_get_json(url: str, *, sleep_s: float = 0.22, retries: int = 4) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        time.sleep(max(0.0, sleep_s if attempt == 0 else min(12.0, 2.0 * (attempt + 1))))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:240]
            last_err = RuntimeError(f"HTTP {exc.code} {url}: {detail}")
            if exc.code in (429, 503) and attempt + 1 < retries:
                continue
            raise last_err from exc
    assert last_err is not None
    raise last_err


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        string_to_sign = f"{ts}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return
    parsed = json.loads(body)
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"Lark rejected: {body[:200]}")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def publish_feed(case: str, events: list[dict[str, Any]]) -> None:
    """写成飞书页能画的 latest.json：ETH 转出日合计。"""
    daily: dict[str, float] = {}
    for ev in events:
        if str(ev.get("asset") or "").upper() != "ETH" or ev.get("direction") != "out":
            continue
        ts = ev.get("ts")
        amt = ev.get("amount")
        if not isinstance(ts, (int, float)) or not isinstance(amt, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + float(amt)
    days = sorted(daily)
    data = [{"time": day, "value": round(daily[day], 6)} for day in days]
    latest_top = [{"symbol": day, "usd": round(daily[day], 6)} for day in days[-8:]]
    months: list[dict[str, Any]] = []
    prev: float | None = None
    for day in days:
        val = daily[day]
        mom = None if prev in (None, 0) else round((val / prev - 1.0) * 100.0, 4)
        months.append({"month": day, "usd": round(val, 6), "mom_pct": mom})
        prev = val
    series = (
        [{"label": "ETH 转出", "labelEn": "ETH out", "months": months}] if len(months) >= 2 else []
    )
    payload = {
        "code": "0",
        "msg": "success",
        "name": "wallet_watch",
        "case": case,
        "updated_at": now_utc_iso(),
        "events": events[-80:],
        "data": data,
        "latest_top": latest_top,
        "series": series,
    }
    written = 0
    for path in FEED_PATHS:
        try:
            save_json(path, payload)
            written += 1
        except OSError:
            continue
    print(f"[wallet-watch] feed days={len(data)} files={written} latest={days[-1] if days else '-'}")


def short_addr(addr: str) -> str:
    if len(addr) <= 14:
        return addr
    return f"{addr[:8]}…{addr[-6:]}"


def eth_norm(addr: str) -> str:
    return addr.strip().lower()


def resolve_watchlist_path(raw: str | None) -> Path:
    if not raw:
        return WATCHLIST_PATH
    p = Path(raw)
    if not p.is_absolute():
        for cand in (HERE / p, Path.cwd() / p):
            if cand.is_file():
                return cand
    return p


def state_paths_for(watchlist: Path) -> tuple[Path, Path]:
    stem = watchlist.stem
    if stem in ("watchlist", ""):
        return OUT_DIR / "state.json", OUT_DIR / "events.json"
    return OUT_DIR / f"state_{stem}.json", OUT_DIR / f"events_{stem}.json"



def eth_account_get(api_key: str | None, params: dict[str, str]) -> list[dict]:
    """优先 Etherscan v2；无 key 时走 Blockscout 同参兼容接口。"""
    if api_key:
        q = {"chainid": "1", "apikey": api_key, **params}
        url = ETHERSCAN_V2 + "?" + urllib.parse.urlencode(q)
        label = "Etherscan"
    else:
        q = dict(params)
        url = BLOCKSCOUT_API + "?" + urllib.parse.urlencode(q)
        label = "Blockscout"
    data = http_get_json(url, sleep_s=1.2 if not api_key else 0.22)
    status = str(data.get("status", ""))
    result = data.get("result")
    if status == "0":
        msg = str(data.get("message") or result or "")
        if "No transactions found" in msg or result == [] or result == "[]":
            return []
        raise RuntimeError(f"{label}: {msg[:200]}")
    return result if isinstance(result, list) else []


def fetch_eth_events(address: str, api_key: str | None, *, page_size: int = 40) -> list[dict]:
    addr = eth_norm(address)
    events: list[dict] = []
    native = eth_account_get(
        api_key,
        {
            "module": "account",
            "action": "txlist",
            "address": addr,
            "page": "1",
            "offset": str(page_size),
            "sort": "desc",
        },
    )
    for row in native:
        if not isinstance(row, dict):
            continue
        if str(row.get("isError", "0")) == "1":
            continue
        value_wei = int(row.get("value") or 0)
        value_eth = value_wei / 1e18
        counterpart = eth_norm(str(row.get("to") or ""))
        direction = "out" if eth_norm(str(row.get("from") or "")) == addr else "in"
        if direction == "in":
            counterpart = eth_norm(str(row.get("from") or ""))
        events.append(
            {
                "chain": "ethereum",
                "watch": addr,
                "tx": str(row.get("hash") or ""),
                "ts": int(row.get("timeStamp") or 0),
                "direction": direction,
                "counterpart": counterpart,
                "asset": "ETH",
                "amount": value_eth,
                "kind": "native",
            }
        )
    tokens = eth_account_get(
        api_key,
        {
            "module": "account",
            "action": "tokentx",
            "address": addr,
            "page": "1",
            "offset": str(page_size),
            "sort": "desc",
        },
    )
    for row in tokens:
        if not isinstance(row, dict):
            continue
        contract = eth_norm(str(row.get("contractAddress") or ""))
        meta = ETH_STABLE.get(contract)
        if meta is None:
            continue
        symbol, decimals = meta
        raw = int(row.get("value") or 0)
        amount = raw / (10**decimals)
        counterpart = eth_norm(str(row.get("to") or ""))
        direction = "out" if eth_norm(str(row.get("from") or "")) == addr else "in"
        if direction == "in":
            counterpart = eth_norm(str(row.get("from") or ""))
        events.append(
            {
                "chain": "ethereum",
                "watch": addr,
                "tx": str(row.get("hash") or ""),
                "ts": int(row.get("timeStamp") or 0),
                "direction": direction,
                "counterpart": counterpart,
                "asset": symbol,
                "amount": amount,
                "kind": "token",
                "contract": contract,
            }
        )
    return events


def fetch_tron_events(address: str, *, limit: int = 40) -> list[dict]:
    events: list[dict] = []
    url = (
        f"{TRONSCAN_API}/transaction"
        f"?sort=-timestamp&count=true&limit={limit}&start=0"
        f"&address={urllib.parse.quote(address)}"
    )
    data = http_get_json(url, sleep_s=0.35)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("contractRet") not in (None, "SUCCESS"):
            # 部分接口用 contractRet
            if row.get("confirmed") is False:
                continue
        to_addr = str(row.get("toAddress") or row.get("to") or "")
        from_addr = str(row.get("ownerAddress") or row.get("from") or "")
        direction = "out" if from_addr == address else "in"
        counterpart = to_addr if direction == "out" else from_addr
        amount_sun = int(row.get("amount") or 0)
        # Tronscan 列表里 amount 单位不统一;合约转账另拉 trc20
        if amount_sun > 0 and not row.get("tokenInfo"):
            events.append(
                {
                    "chain": "tron",
                    "watch": address,
                    "tx": str(row.get("hash") or ""),
                    "ts": int((row.get("timestamp") or 0) // 1000),
                    "direction": direction,
                    "counterpart": counterpart,
                    "asset": "TRX",
                    "amount": amount_sun / 1e6,
                    "kind": "native",
                }
            )

    trc_url = (
        f"{TRONSCAN_API}/token_trc20/transfers"
        f"?limit={limit}&start=0&sort=-timestamp&count=true"
        f"&relatedAddress={urllib.parse.quote(address)}"
    )
    trc = http_get_json(trc_url, sleep_s=0.35)
    trc_rows = trc.get("token_transfers") if isinstance(trc, dict) else None
    if not isinstance(trc_rows, list):
        trc_rows = trc.get("data") if isinstance(trc, dict) else None
    if not isinstance(trc_rows, list):
        trc_rows = []
    for row in trc_rows:
        if not isinstance(row, dict):
            continue
        contract = str(
            row.get("contract_address")
            or (row.get("tokenInfo") or {}).get("tokenId")
            or ""
        )
        meta = TRON_STABLE.get(contract)
        if meta is None:
            # 也认 symbol
            sym = str((row.get("tokenInfo") or {}).get("tokenAbbr") or row.get("tokenName") or "")
            if sym.upper() != "USDT":
                continue
            symbol, decimals = "USDT", int((row.get("tokenInfo") or {}).get("tokenDecimal") or 6)
        else:
            symbol, decimals = meta
        raw = int(row.get("quant") or row.get("amount") or 0)
        amount = raw / (10**decimals)
        from_addr = str(row.get("from_address") or row.get("from") or "")
        to_addr = str(row.get("to_address") or row.get("to") or "")
        direction = "out" if from_addr == address else "in"
        counterpart = to_addr if direction == "out" else from_addr
        events.append(
            {
                "chain": "tron",
                "watch": address,
                "tx": str(row.get("transaction_id") or row.get("hash") or ""),
                "ts": int((row.get("block_ts") or row.get("timestamp") or 0) // 1000),
                "direction": direction,
                "counterpart": counterpart,
                "asset": symbol,
                "amount": amount,
                "kind": "token",
                "contract": contract,
            }
        )
    return events


def fetch_btc_events(address: str, *, limit: int = 50) -> list[dict]:
    url = f"{MEMPOOL_API}/address/{urllib.parse.quote(address)}/txs"
    data = http_get_json(url, sleep_s=0.35)
    if not isinstance(data, list):
        return []
    events: list[dict] = []
    for row in data[:limit]:
        if not isinstance(row, dict):
            continue
        txid = str(row.get("txid") or "")
        status = row.get("status") if isinstance(row.get("status"), dict) else {}
        ts = int(status.get("block_time") or 0)
        vin_sum = 0
        vout_sum = 0
        other_in: dict[str, int] = {}
        other_out: dict[str, int] = {}
        for vin in row.get("vin") or []:
            prev = vin.get("prevout") if isinstance(vin, dict) else None
            if not isinstance(prev, dict):
                continue
            a = str(prev.get("scriptpubkey_address") or "")
            v = int(prev.get("value") or 0)
            if a == address:
                vin_sum += v
            elif a:
                other_in[a] = other_in.get(a, 0) + v
        for vout in row.get("vout") or []:
            if not isinstance(vout, dict):
                continue
            a = str(vout.get("scriptpubkey_address") or "")
            v = int(vout.get("value") or 0)
            if a == address:
                vout_sum += v
            elif a:
                other_out[a] = other_out.get(a, 0) + v
        net = vout_sum - vin_sum
        if net == 0:
            continue
        direction = "in" if net > 0 else "out"
        side = other_in if direction == "in" else other_out
        counterpart = max(side.items(), key=lambda kv: kv[1])[0] if side else ""
        events.append(
            {
                "chain": "bitcoin",
                "watch": address,
                "tx": txid,
                "ts": ts,
                "direction": direction,
                "counterpart": counterpart,
                "asset": "BTC",
                "amount": abs(net) / 1e8,
                "kind": "native",
            }
        )
    return events


def fetch_btc_usd_approx(ts: int | None = None) -> float | None:
    """粗估美元价值；失败返回 None。"""
    try:
        if ts and ts > 0:
            url = f"{MEMPOOL_API}/v1/historical-price?currency=USD&timestamp={int(ts)}"
            data = http_get_json(url, sleep_s=0.15)
            prices = data.get("prices") if isinstance(data, dict) else None
            if isinstance(prices, list) and prices:
                last = prices[-1]
                if isinstance(last, dict) and last.get("USD") is not None:
                    return float(last["USD"])
        data = http_get_json(f"{MEMPOOL_API}/v1/prices", sleep_s=0.15)
        if isinstance(data, dict) and data.get("USD") is not None:
            return float(data["USD"])
    except Exception:  # noqa: BLE001
        return None
    return None


def event_key(ev: dict) -> str:
    return f"{ev.get('chain')}|{ev.get('watch')}|{ev.get('tx')}|{ev.get('asset')}|{ev.get('direction')}|{ev.get('amount')}"


def should_alert(
    ev: dict,
    counterparties: dict[str, dict[str, str]],
    *,
    min_native: float,
    min_stable: float,
    min_btc: float,
) -> tuple[bool, str]:
    chain = str(ev.get("chain") or "")
    asset = str(ev.get("asset") or "")
    amount = float(ev.get("amount") or 0)
    counterpart = str(ev.get("counterpart") or "")
    cp_map = counterparties.get(chain) or {}
    cp_hit = ""
    if chain == "ethereum":
        cp_hit = cp_map.get(eth_norm(counterpart), "")
    elif counterpart:
        cp_hit = cp_map.get(counterpart, "") or cp_map.get(counterpart.lower(), "")

    if cp_hit:
        return True, f"对手方={cp_hit}"

    if asset in ("ETH", "TRX") and amount >= min_native:
        return True, f"金额>={min_native} {asset}"
    if asset == "BTC" and amount >= min_btc:
        return True, f"金额>={min_btc} BTC"
    if asset in ("USDT", "USDC", "BUSD", "USDD") and amount >= min_stable:
        return True, f"金额>={min_stable} {asset}"
    return False, ""


def fmt_btc(amount: float) -> str:
    if amount >= 1000:
        return f"{amount:,.4f}".rstrip("0").rstrip(".")
    return f"{amount:.4f}".rstrip("0").rstrip(".")


def fmt_usd_from_btc(amount: float, px: float | None) -> str:
    if px is None or px <= 0:
        return ""
    usd = amount * px
    if usd >= 1e8:
        return f"，价值约 {usd / 1e8:.2f} 亿美元"
    if usd >= 1e4:
        return f"，价值约 {usd / 1e4:.2f} 万美元"
    return f"，价值约 {usd:,.0f} 美元"


def when_cn(ts: int) -> str:
    if not ts:
        return "时间未知"
    return datetime.fromtimestamp(int(ts), tz=TZ_CN).strftime("%Y-%m-%d %H:%M") + "（UTC+8）"


def format_alert_ledger(ev: dict, label: str, reason: str, cp_name: str) -> str:
    ts = ev.get("ts") or 0
    when = (
        datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts
        else "?"
    )
    chain = str(ev.get("chain") or "")
    watch = str(ev.get("watch") or "")
    tx = str(ev.get("tx") or "")
    direction = "转出" if ev.get("direction") == "out" else "转入"
    amount = float(ev.get("amount") or 0)
    asset = str(ev.get("asset") or "")
    counterpart = str(ev.get("counterpart") or "")
    lines = [
        "【地址监控】Ledger/CryptoBilis 相关资金异动",
        f"链: {chain} | {label}",
        f"地址: {short_addr(watch)}",
        f"{direction} {amount:.6g} {asset} @ {when}",
    ]
    if counterpart:
        extra = f" ({cp_name})" if cp_name else ""
        lines.append(f"对手方: {short_addr(counterpart)}{extra}")
    lines.append(f"触发: {reason}")
    lines.append(f"交易哈希 {tx}")
    lines.append(PAGE)
    return "\n".join(lines)


def format_alert_usg(
    ev: dict,
    *,
    label: str,
    reason: str,
    cp_name: str,
    case_title: str,
    history: bool,
    btc_usd: float | None,
) -> str:
    amount = float(ev.get("amount") or 0)
    direction = "转出" if ev.get("direction") == "out" else "转入"
    watch = str(ev.get("watch") or "")
    counterpart = str(ev.get("counterpart") or "")
    tx = str(ev.get("tx") or "")
    ts = int(ev.get("ts") or 0)
    usd_bit = fmt_usd_from_btc(amount, btc_usd) if ev.get("asset") == "BTC" else ""
    title = f"【历史大额】{case_title}地址异动" if history else f"【链上监控】{case_title}地址异动"
    body = (
        f"据吴说数据中心统计，标记为「{case_title}」的地址"
        f"于 {when_cn(ts)}{direction} {fmt_btc(amount)} 枚 BTC{usd_bit}。"
    )
    if label:
        body += f"监控标签：{label}。"
    if counterpart:
        body += f"主要对手方为 {cp_name or short_addr(counterpart)}。"
    if history:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（发现时间 {found} UTC+8），请勿当成刚刚发生。"
    body += f"监控地址 {short_addr(watch)}。触发：{reason}。"
    return "\n".join([title, "", body, "", f"交易哈希 {tx}", PAGE])


def format_alert(
    ev: dict,
    *,
    label: str,
    reason: str,
    cp_name: str,
    case: str,
    case_title: str,
    history: bool,
    btc_usd: float | None,
) -> str:
    if case == "usg_bitfinex":
        return format_alert_usg(
            ev,
            label=label,
            reason=reason,
            cp_name=cp_name,
            case_title=case_title or "美国政府 Bitfinex 没收资金",
            history=history,
            btc_usd=btc_usd,
        )
    return format_alert_ledger(ev, label, reason, cp_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true", help="有告警时推飞书")
    parser.add_argument("--bootstrap", action="store_true", help="只写入最新游标,不产生告警")
    parser.add_argument(
        "--history-alert",
        action="store_true",
        help="回扫已有交易：超 history-min 记为【历史大额】并推送，同时写入游标",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列地址,不请求")
    parser.add_argument("--watchlist", default="", help="watchlist JSON 路径")
    parser.add_argument("--min-native", type=float, default=1.0, help="ETH/TRX 告警阈值(默认 1)")
    parser.add_argument("--min-stable", type=float, default=10_000.0, help="稳定币告警阈值(默认 10000)")
    parser.add_argument("--min-btc", type=float, default=None, help="BTC 告警阈值(默认 0.1 或名单 defaults)")
    parser.add_argument("--history-min-btc", type=float, default=None, help="历史回扫 BTC 阈值(默认 1000)")
    parser.add_argument(
        "--history-age-hours",
        type=float,
        default=6.0,
        help="日常监控中交易超过该小时数则改用【历史大额】(默认 6)",
    )
    parser.add_argument("--max-alerts", type=int, default=20, help="单轮最多推送条数")
    args = parser.parse_args()

    env = merge_env()
    watch_path = resolve_watchlist_path(args.watchlist or None)
    if not watch_path.is_file():
        raise SystemExit(f"找不到 watchlist: {watch_path}")
    state_path, events_path = state_paths_for(watch_path)

    watch_doc = load_json(watch_path, {})
    defaults = watch_doc.get("defaults") if isinstance(watch_doc.get("defaults"), dict) else {}
    case = str(watch_doc.get("case") or "ledger")
    case_title = str(watch_doc.get("case_title") or "")
    min_btc = float(args.min_btc) if args.min_btc is not None else float(defaults.get("min_btc") or 0.1)
    history_min_btc = (
        float(args.history_min_btc)
        if args.history_min_btc is not None
        else float(defaults.get("history_min_btc") or 1000)
    )

    addresses = [
        a
        for a in (watch_doc.get("addresses") or [])
        if isinstance(a, dict) and a.get("enabled", True) and a.get("address")
    ]
    counterparties_raw = load_json(COUNTERPARTIES_PATH, {})
    counterparties = {
        "ethereum": {eth_norm(k): v for k, v in (counterparties_raw.get("ethereum") or {}).items()},
        "tron": dict(counterparties_raw.get("tron") or {}),
        "bitcoin": dict(counterparties_raw.get("bitcoin") or {}),
    }

    eth_key = (env.get("ETHERSCAN_API_KEY") or "").strip() or None
    eth_budget = 4 if not eth_key else 10_000
    eth_all = [a for a in addresses if a.get("chain") == "ethereum"]
    if eth_all and len(eth_all) > eth_budget:
        start = datetime.now(timezone.utc).hour % len(eth_all)
        rotated = eth_all[start:] + eth_all[:start]
        eth_pick = {id(a) for a in rotated[:eth_budget]}
        addresses = [a for a in addresses if a.get("chain") != "ethereum" or id(a) in eth_pick]
        print(
            f"[wallet-watch] 无 ETHERSCAN_API_KEY：本轮 ETH {eth_budget}/{len(eth_all)} "
            f"(小时轮询), 其余链全量"
        )
    elif eth_all and not eth_key:
        print("[wallet-watch] 未配置 ETHERSCAN_API_KEY，Ethereum 走 Blockscout 公共接口")

    print(f"[wallet-watch] case={case} watchlist={watch_path.name} addresses={len(addresses)}")
    if args.dry_run:
        for a in addresses:
            print(f"  {a.get('chain'):<10} {a.get('address')}  ({a.get('label')})")
        return

    state = load_json(state_path, {"seen": {}, "updated_at": None})
    seen: dict[str, bool] = dict(state.get("seen") or {})
    label_of = {
        eth_norm(str(a["address"])) if a.get("chain") == "ethereum" else str(a["address"]): str(
            a.get("label") or ""
        )
        for a in addresses
    }
    min_btc_of = {
        eth_norm(str(a["address"])) if a.get("chain") == "ethereum" else str(a["address"]): (
            float(a["min_btc"]) if a.get("min_btc") is not None else min_btc
        )
        for a in addresses
    }

    all_new: list[dict] = []
    errors: list[str] = []
    now_ts = int(datetime.now(timezone.utc).timestamp())

    for item in addresses:
        chain = str(item.get("chain") or "")
        address = str(item["address"])
        try:
            if chain == "ethereum":
                batch = fetch_eth_events(address, eth_key)
            elif chain == "tron":
                batch = fetch_tron_events(address)
            elif chain == "bitcoin":
                batch = fetch_btc_events(address)
            else:
                print(f"  skip unknown chain={chain}")
                continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{chain}:{short_addr(address)} {exc}")
            print(f"  FAILED {chain} {short_addr(address)}: {exc}")
            continue

        watch_key = eth_norm(address) if chain == "ethereum" else address
        prefix = f"{chain}|{watch_key}|"
        seeded = any(k.startswith(prefix) for k in seen)
        seed_only = (args.bootstrap or not seeded) and not args.history_alert
        addr_min_btc = min_btc_of.get(watch_key, min_btc)
        fresh = 0
        for ev in batch:
            key = event_key(ev)
            if key in seen:
                continue
            seen[key] = True
            fresh += 1
            amount = float(ev.get("amount") or 0)
            asset = str(ev.get("asset") or "")

            if args.bootstrap and not args.history_alert:
                continue

            if args.history_alert:
                if asset == "BTC" and amount >= history_min_btc:
                    ok, reason = True, f"历史回扫金额>={history_min_btc} BTC"
                else:
                    ok, reason = should_alert(
                        ev,
                        counterparties,
                        min_native=args.min_native,
                        min_stable=args.min_stable,
                        min_btc=max(addr_min_btc, history_min_btc),
                    )
                    if not ok:
                        continue
                ev = dict(ev)
                ev["_reason"] = reason
                ev["_label"] = label_of.get(watch_key, "")
                ev["_history"] = True
                all_new.append(ev)
                continue

            if seed_only:
                continue
            ok, reason = should_alert(
                ev,
                counterparties,
                min_native=args.min_native,
                min_stable=args.min_stable,
                min_btc=addr_min_btc,
            )
            if ok:
                ev = dict(ev)
                ev["_reason"] = reason
                ev["_label"] = label_of.get(watch_key, "")
                age_h = (now_ts - int(ev.get("ts") or now_ts)) / 3600.0 if ev.get("ts") else 0.0
                ev["_history"] = age_h >= float(args.history_age_hours)
                all_new.append(ev)

        mode = (
            "history-alert"
            if args.history_alert
            else ("bootstrap" if args.bootstrap else ("seed" if seed_only else "watch"))
        )
        print(
            f"  {chain:<10} {short_addr(address)} txs_fetched={len(batch)} "
            f"new_keys={fresh} mode={mode}"
        )

    if len(seen) > 20_000:
        keys = list(seen.keys())
        seen = {k: True for k in keys[-10_000:]}

    state = {"seen": seen, "updated_at": now_utc_iso(), "errors": errors, "case": case}
    save_json(state_path, state)

    hist = load_json(events_path, {"events": []})
    hist_events = list(hist.get("events") or [])
    for ev in all_new:
        hist_events.append(
            {
                **{k: v for k, v in ev.items() if not k.startswith("_")},
                "alerted_at": now_utc_iso(),
                "history": bool(ev.get("_history")),
            }
        )
    hist_events = hist_events[-200:]
    save_json(
        events_path,
        {"updated_at": now_utc_iso(), "case": case, "events": hist_events},
    )
    if events_path.name == "events.json":
        publish_feed(str(case or ""), hist_events)

    if args.bootstrap and not args.history_alert:
        print(f"[wallet-watch] bootstrap 完成,seen={len(seen)},不告警 state={state_path.name}")
        return

    print(f"[wallet-watch] 新告警 {len(all_new)} 条,seen={len(seen)} state={state_path.name}")
    if not all_new:
        return

    all_new.sort(key=lambda e: float(e.get("amount") or 0), reverse=True)
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    to_send = all_new[: max(1, args.max_alerts)]
    btc_usd_cache: dict[int, float | None] = {}

    for ev in to_send:
        cp_map = counterparties.get(str(ev.get("chain")) or "") or {}
        counterpart = str(ev.get("counterpart") or "")
        if ev.get("chain") == "ethereum":
            cp_name = cp_map.get(eth_norm(counterpart), "")
        else:
            cp_name = cp_map.get(counterpart, "") or cp_map.get(counterpart.lower(), "")
        btc_usd = None
        if ev.get("asset") == "BTC":
            bucket = int(ev.get("ts") or 0) // 3600
            if bucket not in btc_usd_cache:
                btc_usd_cache[bucket] = fetch_btc_usd_approx(int(ev.get("ts") or 0) or None)
            btc_usd = btc_usd_cache[bucket]
        text = format_alert(
            ev,
            label=str(ev.get("_label") or ""),
            reason=str(ev.get("_reason") or ""),
            cp_name=cp_name,
            case=case,
            case_title=case_title,
            history=bool(ev.get("_history")),
            btc_usd=btc_usd,
        )
        print("---")
        print(text)
        if args.push:
            if not webhook:
                print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
                raise SystemExit(2)
            send_lark_text(webhook, secret, text)
            print("  → Lark OK")
            time.sleep(0.4)

    if len(all_new) > len(to_send):
        print(f"[wallet-watch] 另有 {len(all_new) - len(to_send)} 条未推送(受 --max-alerts 限制)")


if __name__ == "__main__":
    main()
