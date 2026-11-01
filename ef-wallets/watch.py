"""以太坊基金会「关联钱包」大额转入交易所。

盯两个公开报道过的地址（EmberCN / Lookonchain 口径，不是基金会官方金库）。
数据：
  - CoinGlass 交易所链上转入（带来源交易所名，每小时最多 3 次）
  - 无 Key 或接口失败时，用 Etherscan/Blockscout 补「大额转出」，页面标未标注，不推 Lark

网页展示近期大额记录。Lark 只在「同一上海日、同一交易所、新发生的转入合计 ≥ 1000 ETH」时发一条新闻，不逐笔流水。

Usage:
  python watch.py --bootstrap   # 记已见，不推历史
  python watch.py --push        # 有新的超阈值才推
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
OUT_DIR = HERE / "output" / "json"
STATE_PATH = HERE / "output" / "state.json"
TRANSFERS_PATH = OUT_DIR / "transfers.json"

CG_URL = "https://open-api-v4.coinglass.com/api/exchange/chain/tx/list"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
BLOCKSCOUT_API = "https://eth.blockscout.com/api"
DASHBOARD_URL = "https://data.wublockchain.xyz/dashboard/ef-linked-wallets"
SHANGHAI = timezone(timedelta(hours=8))

# 公开报道：0x4c4…2b94、0x999…0229（第二条完整地址以 in-crypto 公布为准，尾号为 0229c）
WATCH = {
    "0x4c4f7b7c53357616ebddd6b901d55b7f9b5d2b94": "关联钱包 A",
    "0x999d5f1fcfcc36a840dd726709bcf38b6b20229c": "关联钱包 B",
}

LOOKBACK_DAYS = 21
PAGE_MIN_ETH = 500
LARK_MIN_ETH = 1000
# 公开热钱包。充值地址本身常常没名字，但几分钟内会归集到这里。
HOT_WALLETS = {
    "0xf30ba13e4b04ce5dc4d254ae5fa95477800f0eb0": "Kraken",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "Kraken",
    "0xae2d4617c862309a3d75a0ffb358c7a5009c673f": "Kraken",
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
}
CG_MIN_USD = 1_000_000
CG_MAX_PAGES = 3
HTTP_TIMEOUT = 40


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

def now_iso() -> str:
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
    env: dict[str, str] = {}
    for path in (
        Path("/root/dashboard/.env.live"),
        ROOT.parent / "data-dashboard" / ".env.live",
        ROOT / ".env",
    ):
        env.update(load_env(path))
    for k in ("COINGLASS_API_KEY", "ETHERSCAN_API_KEY", "LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "ef-wallets/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def norm(addr: str) -> str:
    return addr.strip().lower()


def as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def as_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def shanghai_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=SHANGHAI).strftime("%Y-%m-%d")


def fmt_eth(n: float) -> str:
    if abs(n - round(n)) < 0.05:
        return f"{round(n):,}"
    return f"{n:,.1f}"


def fmt_usd_cn(usd: float) -> str:
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    wan = usd / 1e4
    if wan >= 100:
        return f"{wan:,.0f} 万美元"
    return f"{wan:.1f} 万美元"


def fmt_date_cn(day: str) -> str:
    parts = day.split("-")
    if len(parts) != 3:
        return day
    return f"{int(parts[1])}月{int(parts[2])}日"


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        payload["sign"] = base64.b64encode(digest).decode()
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


def fetch_coinglass(api_key: str) -> tuple[list[dict], str | None]:
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000)
    rows: list[dict] = []
    for page in range(1, CG_MAX_PAGES + 1):
        q = urllib.parse.urlencode(
            {
                "symbol": "ETH",
                "min_usd": str(CG_MIN_USD),
                "start_time": str(start_ms),
                "per_page": "100",
                "page": str(page),
            }
        )
        try:
            data = http_json(f"{CG_URL}?{q}", {"CG-API-KEY": api_key, "accept": "application/json"})
        except Exception as exc:  # noqa: BLE001
            return rows, f"coinglass: {exc}"
        if str(data.get("code", "0")) not in ("0", "200"):
            return rows, f"coinglass code={data.get('code')} msg={data.get('msg')}"
        batch = data.get("data") or []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < 100:
            break
        time.sleep(0.3)
    return rows, None


def fetch_chain(address: str, api_key: str | None) -> tuple[list[dict], str | None]:
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "page": "1",
        "offset": "80",
        "sort": "desc",
    }
    if api_key:
        params["chainid"] = "1"
        params["apikey"] = api_key
        url = ETHERSCAN_V2 + "?" + urllib.parse.urlencode(params)
        label = "etherscan"
    else:
        url = BLOCKSCOUT_API + "?" + urllib.parse.urlencode(params)
        label = "blockscout"
    try:
        data = http_json(url)
    except Exception as exc:  # noqa: BLE001
        return [], f"{label}: {exc}"
    result = data.get("result")
    if str(data.get("status", "1")) == "0":
        msg = str(data.get("message") or result or "")
        if "No transactions" in msg:
            return [], None
        return [], f"{label}: {msg[:180]}"
    return result if isinstance(result, list) else [], None


def row_from_cg(raw: dict) -> dict | None:
    frm = norm(str(raw.get("from_address") or ""))
    if frm not in WATCH:
        return None
    if str(raw.get("transfer_type")) not in ("1", "1.0"):
        return None
    symbol = str(raw.get("asset_symbol") or "").upper()
    if symbol not in ("ETH", "WETH"):
        return None
    eth = as_float(raw.get("asset_quantity"))
    usd = as_float(raw.get("amount_usd"))
    if eth < PAGE_MIN_ETH and usd < CG_MIN_USD:
        return None
    ts = as_int(raw.get("transaction_time"))
    if ts > 10_000_000_000:
        ts //= 1000
    tx = str(raw.get("transaction_hash") or "")
    if not tx:
        return None
    return {
        "tx": tx,
        "ts": ts,
        "date": shanghai_date(ts) if ts else "",
        "from": frm,
        "label": WATCH[frm],
        "to": norm(str(raw.get("to_address") or "")),
        "exchange": str(raw.get("exchange_name") or "").strip() or "未标注",
        "eth": eth,
        "usd": usd,
        "source": "coinglass",
    }


def row_from_chain(address: str, raw: dict) -> dict | None:
    if str(raw.get("isError", "0")) == "1":
        return None
    frm = norm(str(raw.get("from") or ""))
    if frm != address:
        return None
    eth = int(raw.get("value") or 0) / 1e18
    if eth < PAGE_MIN_ETH:
        return None
    ts = as_int(raw.get("timeStamp"))
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp())
    if ts and ts < cutoff:
        return None
    tx = str(raw.get("hash") or "")
    if not tx:
        return None
    return {
        "tx": tx,
        "ts": ts,
        "date": shanghai_date(ts) if ts else "",
        "from": frm,
        "label": WATCH[frm],
        "to": norm(str(raw.get("to") or "")),
        "exchange": "未标注",
        "eth": eth,
        "usd": 0.0,
        "source": "chain",
    }


def label_by_sweep(to_addr: str, eth: float, ts: int, api_key: str | None, cache: dict[str, str]) -> str:
    """充值地址若很快把相近金额转到已知热钱包，就标成那家交易所。"""
    key = norm(to_addr)
    if not key:
        return "未标注"
    if key in HOT_WALLETS:
        return HOT_WALLETS[key]
    if key in cache:
        return cache[key] or "未标注"
    rows, err = fetch_chain(key, api_key)
    if err or not rows:
        cache[key] = ""
        return "未标注"
    for raw in rows:
        if norm(str(raw.get("from") or "")) != key:
            continue
        dest = norm(str(raw.get("to") or ""))
        if dest not in HOT_WALLETS:
            continue
        moved = int(raw.get("value") or 0) / 1e18
        when = as_int(raw.get("timeStamp"))
        if abs(moved - eth) / max(eth, 1) > 0.02:
            continue
        if ts and when and not (0 <= when - ts <= 6 * 3600):
            continue
        cache[key] = HOT_WALLETS[dest]
        return HOT_WALLETS[dest]
    cache[key] = ""
    return "未标注"


def news_text(day: str, exchange: str, rows: list[dict]) -> str:
    eth = sum(r["eth"] for r in rows)
    usd = sum(r["usd"] for r in rows)
    wallets = len({r["from"] for r in rows})
    usd_bit = f"（约 {fmt_usd_cn(usd)}）" if usd > 0 else ""
    return "\n".join(
        [
            (
                f"据吴说数据中心统计，{fmt_date_cn(day)}，{wallets} 个以太坊基金会关联钱包"
                f"向 {exchange} 转入合计约 {fmt_eth(eth)} 枚 ETH{usd_bit}。"
            ),
            "转入交易所只说明资金到达交易所地址，不能据此认定已经卖出。上述「关联」来自公开链上标注，并非基金会官方确认。",
            f"数据来源：CoinGlass 交易所链上转账｜吴说数据中心",
            DASHBOARD_URL,
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="把当前记录记为已见，不推 Lark")
    args = parser.parse_args()

    env = merge_env()
    errors: list[str] = []
    by_tx: dict[str, dict] = {}

    cg_key = (env.get("COINGLASS_API_KEY") or "").strip()
    if cg_key:
        cg_rows, err = fetch_coinglass(cg_key)
        if err:
            errors.append(err)
        for raw in cg_rows:
            row = row_from_cg(raw)
            if row:
                by_tx[row["tx"].lower()] = row
        print(f"[ef-wallets] coinglass rows={len(cg_rows)} matched={len(by_tx)}")
    else:
        errors.append("no COINGLASS_API_KEY")
        print("[ef-wallets] no COINGLASS_API_KEY")

    eth_key = (env.get("ETHERSCAN_API_KEY") or "").strip() or None
    for addr in WATCH:
        chain_rows, err = fetch_chain(addr, eth_key)
        if err:
            errors.append(err)
        added = 0
        for raw in chain_rows:
            row = row_from_chain(addr, raw)
            if not row:
                continue
            key = row["tx"].lower()
            prev = by_tx.get(key)
            if prev and prev.get("exchange") not in ("", "未标注"):
                if not prev.get("eth"):
                    prev["eth"] = row["eth"]
                continue
            if prev:
                prev["eth"] = prev.get("eth") or row["eth"]
                prev["to"] = prev.get("to") or row["to"]
            else:
                by_tx[key] = row
                added += 1
        print(f"[ef-wallets] chain {addr[:10]} txs={len(chain_rows)} new_unlabeled={added}")
        time.sleep(0.4)

    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    label_cache = dict(state.get("labels") or {})
    eth_key_label = eth_key
    for row in by_tx.values():
        if row.get("exchange") not in ("", "未标注"):
            continue
        row["exchange"] = label_by_sweep(row.get("to") or "", float(row.get("eth") or 0), int(row.get("ts") or 0), eth_key_label, label_cache)
        if row["exchange"] != "未标注":
            row["source"] = "sweep"
            print(f"[ef-wallets] labeled {row['tx'][:10]} -> {row['exchange']}")

    fresh_txs = {tx for tx in by_tx}
    if TRANSFERS_PATH.exists():
        try:
            prev = json.loads(TRANSFERS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
        for old in prev.get("rows") or []:
            if not isinstance(old, dict):
                continue
            tx = str(old.get("tx") or "").lower()
            if tx and tx not in by_tx:
                by_tx[tx] = old
    rows = sorted(by_tx.values(), key=lambda r: r.get("ts") or 0, reverse=True)
    sent = set(state.get("sent") or [])

    alertable = [
        r
        for r in rows
        if r["tx"].lower() in fresh_txs
        and r.get("exchange") not in ("", "未标注")
        and r.get("eth", 0) >= 0
        and r["tx"].lower() not in sent
    ]
    # 单笔不够 1000 的先攒进分组，分组合计够了才发
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in alertable:
        groups.setdefault((r["date"], r["exchange"]), []).append(r)

    pushed: list[str] = []
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    for (day, exchange), group in groups.items():
        total = sum(r["eth"] for r in group)
        if total < LARK_MIN_ETH:
            continue
        text = news_text(day, exchange, group)
        print("---")
        print(text)
        if args.bootstrap:
            continue
        if not args.push:
            continue
        if not webhook:
            print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
            raise SystemExit(2)
        send_lark(webhook, secret, text)
        pushed.append(f"{day}|{exchange}|{fmt_eth(total)}")
        for r in group:
            sent.add(r["tx"].lower())
        time.sleep(0.4)

    if args.bootstrap:
        for r in rows:
            sent.add(r["tx"].lower())
        print(f"[ef-wallets] bootstrap seen={len(sent)} 不推送")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "code": "0",
        "updated_at": now_iso(),
        "threshold_eth": LARK_MIN_ETH,
        "page_min_eth": PAGE_MIN_ETH,
        "note": "关联钱包来自公开链上标注，不是基金会官方金库。转入交易所不等于已经卖出。",
        "wallets": [{"address": a, "label": lab} for a, lab in WATCH.items()],
        "errors": errors,
        "rows": rows,
    }
    TRANSFERS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {"sent": sorted(sent), "labels": label_cache, "updated_at": now_iso(), "pushed": pushed},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[ef-wallets] rows={len(rows)} errors={errors or 'none'} pushed={pushed or 'none'}")


if __name__ == "__main__":
    main()
