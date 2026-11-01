#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YZi Labs 标注地址 ENA 大额转出（免费 Etherscan/Blockscout）。

地址 0x877b…4bba6 参考 Arkham「YZi Labs」标签（非 Arkham API）。
监控该地址 ENA 转出；按上海日汇总，日合计≥阈值发一条新闻体。
对手方若命中公开币安热钱包名单则标明「币安」；存款地址会轮换，不全靠名单。
--bootstrap 记已见交易，不补推 10/13 等历史。

用法：
  python monitor_yzi_ena.py --bootstrap
  python monitor_yzi_ena.py --push
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
STATE_PATH = OUT_DIR / "state_yzi_ena.json"
EVENTS_PATH = OUT_DIR / "events_yzi_ena.json"
COUNTERPARTIES_PATH = HERE / "counterparties.json"

WATCH = "0x877b3D5c681C8890d19dbF450306Caa3C3D4bba6"
WATCH_LABEL = "公开标注 YZi Labs（Arkham 参考）"
ENA = "0x57e114B691Db790C35207b2e685D4A43181e6061"
ENA_DECIMALS = 18
ARKHAM_URL = f"https://arkm.com/explorer/address/{WATCH}"
ETHERSCAN_ADDR = f"https://etherscan.io/address/{WATCH}"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
BLOCKSCOUT_API = "https://eth.blockscout.com/api"
CG_PRICE = "https://api.coingecko.com/api/v3/simple/price?ids=ethena&vs_currencies=usd"
# 免费公共 RPC（绕开 Blockscout 429）；Transfer topic0
RPC_URLS = (
    "https://rpc.mevblocker.io",
    "https://ethereum.publicnode.com",
    "https://eth.drpc.org",
    "https://rpc.flashbots.net",  # 可能 prune 较深历史
    "https://1rpc.io/eth",
    "https://eth.llamarpc.com",
)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LOOKBACK_BLOCKS = 120_000  # ~两周+
LOG_CHUNK = 2_000

# 单日合计转入币安 ≥ 此枚数才发（约对应百万美元量级，随币价浮动）
MIN_DAY_ENA = 5_000_000.0
PAGE_SIZE = 100
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/wallet-watch"
USER_AGENT = "wallet-watch-yzi-ena/1.0"

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def eth_norm(addr: str) -> str:
    return addr.strip().lower()


def short_addr(addr: str) -> str:
    a = eth_norm(addr)
    if len(a) < 12:
        return a
    return f"{a[:6]}…{a[-4:]}"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (
        Path("/root/dashboard/.env.live"),
        ROOT.parent / "data-dashboard" / ".env.live",
        ROOT / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ETHERSCAN_API_KEY", "LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def http_json(url: str, *, sleep_s: float = 0.3, retries: int = 4) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        time.sleep(sleep_s if attempt == 0 else min(20.0, 3.0 * (attempt + 1)))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503) and attempt + 1 < retries:
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt + 1 < retries:
                continue
            raise
    raise RuntimeError(last)


def eth_get(api_key: str | None, params: dict[str, str]) -> list[dict] | str:
    if api_key:
        q = {"chainid": "1", "apikey": api_key, **params}
        url = ETHERSCAN_V2 + "?" + urllib.parse.urlencode(q)
        label = "Etherscan"
    else:
        url = BLOCKSCOUT_API + "?" + urllib.parse.urlencode(params)
        label = "Blockscout"
    data = http_json(url, sleep_s=1.0 if not api_key else 0.25)
    status = str(data.get("status", ""))
    result = data.get("result")
    if status == "0":
        msg = str(data.get("message") or result or "")
        if "No transactions found" in msg or result in ([], "[]", None):
            return []
        raise RuntimeError(f"{label}: {msg[:200]}")
    return result if isinstance(result, (list, str)) else []


def load_binance() -> set[str]:
    raw = json.loads(COUNTERPARTIES_PATH.read_text(encoding="utf-8"))
    eth = raw.get("ethereum") or {}
    out: set[str] = set()
    for addr, name in eth.items():
        if "binance" in str(name).lower():
            out.add(eth_norm(addr))
    return out


def rpc_call(method: str, params: list[Any], *, prefer: str | None = None) -> tuple[Any, str]:
    last: Exception | None = None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    urls = list(RPC_URLS)
    if prefer and prefer in urls:
        urls = [prefer] + [u for u in urls if u != prefer]
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                doc = json.loads(resp.read().decode("utf-8", "replace"))
            if doc.get("error"):
                raise RuntimeError(doc["error"])
            if "result" not in doc:
                raise RuntimeError("no result")
            return doc.get("result"), url
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise RuntimeError(f"RPC 失败: {last}")


def topic_addr(addr: str) -> str:
    return "0x" + eth_norm(addr).replace("0x", "").rjust(64, "0")


def fetch_ena_outs_rpc() -> list[dict[str, Any]]:
    latest_hex, rpc_url = rpc_call("eth_blockNumber", [])
    latest = int(latest_hex, 16)
    start = max(0, latest - LOOKBACK_BLOCKS)
    logs: list[Any] = []
    cur = start
    while cur <= latest:
        end = min(latest, cur + LOG_CHUNK - 1)
        # 1rpc 等限制 ≤50 块：该端失败会自动换下一端
        chunk_size = 50 if "1rpc.io" in rpc_url else LOG_CHUNK
        end = min(latest, cur + chunk_size - 1)
        chunk, rpc_url = rpc_call(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(cur),
                    "toBlock": hex(end),
                    "address": ENA,
                    "topics": [TRANSFER_TOPIC, topic_addr(WATCH)],
                }
            ],
            prefer=rpc_url,
        )
        if isinstance(chunk, list):
            logs.extend(chunk)
        cur = end + 1
        time.sleep(0.05)
    out: list[dict[str, Any]] = []
    block_ts: dict[str, int] = {}
    for log in logs:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        to_addr = "0x" + str(topics[2])[-40:]
        raw = int(str(log.get("data") or "0x0"), 16)
        amount = raw / (10**ENA_DECIMALS)
        bn = str(log.get("blockNumber") or "")
        if bn and bn not in block_ts:
            block, rpc_url = rpc_call("eth_getBlockByNumber", [bn, False], prefer=rpc_url)
            ts_hex = (block or {}).get("timestamp") if isinstance(block, dict) else None
            block_ts[bn] = int(ts_hex, 16) if isinstance(ts_hex, str) else 0
            time.sleep(0.03)
        ts = block_ts.get(bn, 0)
        day = datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d") if ts else ""
        out.append(
            {
                "tx": str(log.get("transactionHash") or ""),
                "ts": ts,
                "day": day,
                "to": eth_norm(to_addr),
                "amount": amount,
            }
        )
    out.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
    return out[:PAGE_SIZE]


def fetch_ena_outs(api_key: str | None) -> list[dict[str, Any]]:
    try:
        rows = fetch_ena_outs_rpc()
        # RPC 成功即使空列表也不回落到易 429 的 explorer
        print(f"[yzi-ena] source=rpc n={len(rows)}")
        return rows
    except Exception as exc:  # noqa: BLE001
        print(f"[yzi-ena] rpc outs skip: {exc}")
    watch = eth_norm(WATCH)
    ena = eth_norm(ENA)
    rows = eth_get(
        api_key,
        {
            "module": "account",
            "action": "tokentx",
            "contractaddress": ENA,
            "address": WATCH,
            "page": "1",
            "offset": str(PAGE_SIZE),
            "sort": "desc",
        },
    )
    if not isinstance(rows, list):
        return []
    print(f"[yzi-ena] source=explorer n={len(rows)}")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if eth_norm(str(row.get("contractAddress") or "")) != ena:
            continue
        if eth_norm(str(row.get("from") or "")) != watch:
            continue
        raw = int(row.get("value") or 0)
        amount = raw / (10**ENA_DECIMALS)
        ts = int(row.get("timeStamp") or 0)
        day = datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d") if ts else ""
        out.append(
            {
                "tx": str(row.get("hash") or ""),
                "ts": ts,
                "day": day,
                "to": eth_norm(str(row.get("to") or "")),
                "amount": amount,
            }
        )
    return out


def fetch_ena_balance(api_key: str | None) -> float | None:
    # balanceOf(address)
    data = "0x70a08231" + eth_norm(WATCH).replace("0x", "").rjust(64, "0")
    try:
        raw, _ = rpc_call("eth_call", [{"to": ENA, "data": data}, "latest"])
        if isinstance(raw, str) and raw.startswith("0x"):
            return int(raw, 16) / (10**ENA_DECIMALS)
    except Exception as exc:  # noqa: BLE001
        print(f"[yzi-ena] rpc balance skip: {exc}")
    result = eth_get(
        api_key,
        {
            "module": "account",
            "action": "tokenbalance",
            "contractaddress": ENA,
            "address": WATCH,
            "tag": "latest",
        },
    )
    if isinstance(result, str) and result.isdigit():
        return int(result) / (10**ENA_DECIMALS)
    try:
        return int(str(result)) / (10**ENA_DECIMALS)
    except (TypeError, ValueError):
        return None


def fetch_ena_usd() -> float | None:
    try:
        data = http_json(CG_PRICE, sleep_s=0.2)
        val = ((data or {}).get("ethena") or {}).get("usd")
        return float(val) if isinstance(val, (int, float)) else None
    except Exception:  # noqa: BLE001
        return None


def yi_usd(usd: float) -> str:
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if usd >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:,.0f} 美元"


def fmt_ena(n: float) -> str:
    if abs(n) >= 1e6:
        return f"{n / 1e4:,.0f} 万"
    return f"{n:,.0f}"


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"seen": {}, "sentDays": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seen": {}, "sentDays": []}
    seen = doc.get("seen") if isinstance(doc, dict) else {}
    days = doc.get("sentDays") if isinstance(doc, dict) else []
    return {
        "seen": dict(seen) if isinstance(seen, dict) else {},
        "sentDays": [d for d in days if isinstance(d, str)] if isinstance(days, list) else [],
    }


def save_state(state: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_news(
    day: str,
    total: float,
    n_tx: int,
    balance: float | None,
    px: float | None,
    *,
    binance_ena: float,
    top_tos: list[tuple[str, float, str]],
) -> str:
    y, m, d = day.split("-")
    day_cn = f"{y} 年 {int(m)} 月 {int(d)} 日"
    usd_bit = ""
    if px and px > 0:
        usd_bit = f"，按现价约合 {yi_usd(total * px)}"
    bal_bit = ""
    if balance is not None:
        bal_usd = f"（约 {yi_usd(balance * px)}）" if px and px > 0 else ""
        bal_bit = f"该地址仍持有约 {fmt_ena(balance)} 枚 ENA{bal_usd}。"
    if binance_ena >= MIN_DAY_ENA * 0.5:
        dest = f"向公开币安热钱包相关地址转入合计约 {fmt_ena(binance_ena)} 枚 ENA"
        if total - binance_ena > 1:
            dest += f"（当日该地址 ENA 转出合计约 {fmt_ena(total)} 枚）"
    else:
        dest = f"转出合计约 {fmt_ena(total)} 枚 ENA"
        if top_tos:
            bits = []
            for addr, amt, tag in top_tos[:3]:
                label = tag or short_addr(addr)
                bits.append(f"{label} 约 {fmt_ena(amt)} 枚")
            dest += f"；主要对手方：{'；'.join(bits)}"
    return (
        f"吴说获悉，据吴说数据中心统计，{day_cn}（UTC+8），"
        f"标记为「{WATCH_LABEL}」的地址分 {n_tx} 笔{dest}{usd_bit}。"
        f"{bal_bit}"
        f"口径为 Etherscan/Blockscout 链上 ENA 转账；"
        f"币安识别仅覆盖公开热钱包名单（存款地址会轮换）；"
        f"实体标签参考 Arkham，非 Arkham API。转出不等于已卖出。"
        f"\n{ARKHAM_URL}\n{ETHERSCAN_ADDR}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--force-day", default="", help="强制用该日汇总试推（调试）")
    args = parser.parse_args()

    env = load_env()
    api_key = (env.get("ETHERSCAN_API_KEY") or "").strip() or None
    binance = load_binance()
    print(f"[yzi-ena] watch={short_addr(WATCH)} binance_cps={len(binance)} key={'yes' if api_key else 'no'}")

    try:
        outs = fetch_ena_outs(api_key)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        # 限流时若已有 bootstrap 状态，本轮跳过不报错退出码，避免小时任务刷红
        if STATE_PATH.is_file():
            print(f"[WARN] 拉数失败，跳过本轮: {exc}", file=sys.stderr)
            return 0
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1

    print(f"[yzi-ena] ena_out={len(outs)} binance_hit={sum(1 for r in outs if r.get('to') in binance)}")

    state = load_state()
    seen = state["seen"]

    if args.bootstrap:
        for row in outs:
            tx = str(row.get("tx") or "")
            if tx:
                seen[tx] = True
        keys = list(seen.keys())
        if len(keys) > 5000:
            seen = {k: True for k in keys[-3000:]}
        state["seen"] = seen
        save_state(state)
        print(f"[yzi-ena] bootstrap seen={len(seen)} 不告警")
        return 0

    # 新 ENA 转出按日汇总（任意对手方；币安热钱包单独统计）
    by_day: dict[str, list[dict[str, Any]]] = {}
    new_keys = 0
    for row in outs:
        tx = str(row.get("tx") or "")
        day = str(row.get("day") or "")
        if not tx or not day:
            continue
        if tx in seen:
            continue
        seen[tx] = True
        new_keys += 1
        by_day.setdefault(day, []).append(row)

    state["seen"] = seen
    print(f"[yzi-ena] new_outs={new_keys} days={sorted(by_day)}")

    balance = None
    px = None
    try:
        balance = fetch_ena_balance(api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"[yzi-ena] balance skip: {exc}")
    try:
        px = fetch_ena_usd()
    except Exception as exc:  # noqa: BLE001
        print(f"[yzi-ena] price skip: {exc}")

    candidates: list[tuple[str, float, int, float, list[tuple[str, float, str]]]] = []
    for day, rows in by_day.items():
        total = sum(float(r["amount"]) for r in rows)
        if total < MIN_DAY_ENA and not (args.force_day and day == args.force_day):
            continue
        bn_amt = sum(float(r["amount"]) for r in rows if r.get("to") in binance)
        by_to: dict[str, float] = {}
        for r in rows:
            to = str(r.get("to") or "")
            by_to[to] = by_to.get(to, 0.0) + float(r["amount"])
        top = sorted(by_to.items(), key=lambda kv: -kv[1])[:5]
        top_tos = [
            (addr, amt, "公开币安热钱包" if addr in binance else "")
            for addr, amt in top
        ]
        candidates.append((day, total, len(rows), bn_amt, top_tos))
    candidates.sort(key=lambda x: x[0])

    if not candidates:
        save_state(state)
        print("[skip] 无新达标日")
        return 0

    day, total, n_tx, bn_amt, top_tos = candidates[-1]
    msg_id = f"yzi-ena-{day}"
    if day in state["sentDays"] and not args.force_day:
        save_state(state)
        print(f"[skip] already {msg_id}")
        return 0

    text = as_news(
        build_news(
            day, total, n_tx, balance, px, binance_ena=bn_amt, top_tos=top_tos
        )
    )
    print("---")
    print(msg_id)
    print(text)

    events = []
    if EVENTS_PATH.is_file():
        try:
            events = list((json.loads(EVENTS_PATH.read_text(encoding="utf-8")) or {}).get("events") or [])
        except (OSError, json.JSONDecodeError):
            events = []
    events.append(
        {
            "day": day,
            "ena": total,
            "binance_ena": bn_amt,
            "n_tx": n_tx,
            "balance": balance,
            "px": px,
            "at": now_iso(),
        }
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(
        json.dumps({"updated_at": now_iso(), "events": events[-80:]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    if not args.push:
        save_state(state)
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    print(f"[pushed] {msg_id}")
    state["sentDays"] = (state["sentDays"] + [day])[-60:]
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
