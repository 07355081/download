"""公开浏览器标注的 Multicoin 地址，大额转出原生 HYPE。

只盯一个已经在 HyperEVM 浏览器上标成 Multicoin Capital 的地址。
对手方 0xA9Db…642F 是同一批公开监测反复提到的收款地址，页面写成
「公开监测所称 Coinbase Prime 收款地址」，不是 Coinbase 官方确认。

网页展示这次免费查询已经返回的转出，单笔 ≥ 1 万枚。不再按 120 天丢掉。
Lark：
  - 新发生、同一上海日、同一对手方合计 ≥ 5 万枚，发一条链上消息。系统地址不推。
  - 近 28 日打进该收款地址的累计，高于更早一段不重叠的 28 日窗口，另发【历史阈值警报】。
历史交易用 --bootstrap 记为已见，不补推。

Usage:
  python watch.py --bootstrap
  python watch.py --push
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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = HERE / "output" / "json"
STATE_PATH = HERE / "output" / "state.json"
TRANSFERS_PATH = OUT_DIR / "transfers.json"

EXPLORER = "https://rpc.hyperliquid.xyz/explorer"
INFO = "https://api.hyperliquid.xyz/info"
DASHBOARD_URL = "https://data.wublockchain.xyz/dashboard/multicoin-hype"
TX_URL = "https://app.hyperliquid.xyz/explorer/tx/"
SHANGHAI = timezone(timedelta(hours=8))

SENDER = "0xD4d56a30a4a745f8Ba732E8B453b7066260fBC10"
SENDER_LABEL = "公开浏览器标注 Multicoin Capital"
DEPOSIT = "0xA9Db412084ff49018a378C4AdaCf62BB41Ca642F"
DEPOSIT_LABEL = "公开监测所称 Coinbase Prime 收款地址"
SYSTEM = "0x2222222222222222222222222222222222222222"

PAGE_MIN_HYPE = 10_000
LARK_MIN_HYPE = 50_000
HIST_DAYS = 28
HIST_MIN_HYPE = 200_000
HTTP_TIMEOUT = 40
MARK_HIST = "【历史阈值警报】"


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
    for k in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def post_json(url: str, body: dict) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "multicoin-hype/1.0"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def norm(addr: str) -> str:
    return addr.strip().lower()


def shanghai_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=SHANGHAI).strftime("%Y-%m-%d")


def parse_day(day: str) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d")


def fmt_hype(n: float) -> str:
    if abs(n - round(n)) < 0.05:
        return f"{round(n):,}"
    return f"{n:,.1f}"


def fmt_usd_cn(usd: float) -> str:
    if usd <= 0:
        return ""
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


def counterparty_label(addr: str) -> str:
    key = norm(addr)
    if key == norm(DEPOSIT):
        return DEPOSIT_LABEL
    if key == norm(SYSTEM):
        return "系统地址"
    return "未标注对手方"


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


def decode_to_value(raw: str) -> tuple[str | None, int | None]:
    """EIP-1559 raw tx: destination and native value. Returns (to, wei)."""
    data = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)
    for i in range(len(data) - 22):
        if data[i] != 0x94:
            continue
        to = "0x" + data[i + 1 : i + 21].hex()
        marker = data[i + 21]
        if marker == 0x80:
            return to, 0
        if 0x81 <= marker <= 0xB7:
            length = marker - 0x80
            end = i + 22 + length
            if end > len(data):
                continue
            value = int.from_bytes(data[i + 22 : end], "big")
            if value > 10**15:
                return to, value
    return None, None


def hype_mid() -> float:
    try:
        data = post_json(INFO, {"type": "allMids"})
    except Exception as exc:  # noqa: BLE001
        print(f"[multicoin-hype] price skipped: {exc}")
        return 0.0
    raw = data.get("HYPE") if isinstance(data, dict) else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def fetch_transfers() -> list[dict]:
    payload = post_json(EXPLORER, {"type": "userDetails", "user": SENDER})
    txs = payload.get("txs") if isinstance(payload, dict) else None
    if not isinstance(txs, list):
        raise RuntimeError("explorer userDetails 没有 txs")
    rows: list[dict] = []
    for tx in txs:
        action = tx.get("action") or {}
        if action.get("type") != "evmRawTx":
            continue
        to, wei = decode_to_value(action.get("data") or "")
        if not to or not wei:
            continue
        hype = wei / 10**18
        if hype < PAGE_MIN_HYPE:
            continue
        ms = int(tx.get("time") or 0)
        txh = str(tx.get("hash") or "")
        if not txh:
            continue
        rows.append(
            {
                "tx": txh,
                "ts": ms,
                "date": shanghai_date(ms),
                "from": SENDER,
                "from_label": SENDER_LABEL,
                "to": to,
                "counterparty": counterparty_label(to),
                "hype": round(hype, 4),
            }
        )
    return rows


def load_saved_rows() -> list[dict]:
    if not TRANSFERS_PATH.exists():
        return []
    try:
        payload = json.loads(TRANSFERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("tx")]


def merge_rows(fresh: list[dict], saved: list[dict]) -> list[dict]:
    by_tx: dict[str, dict] = {}
    for row in saved + fresh:
        by_tx[str(row["tx"]).lower()] = row
    kept = []
    for row in by_tx.values():
        try:
            parse_day(str(row.get("date") or ""))
        except ValueError:
            continue
        if float(row.get("hype") or 0) >= PAGE_MIN_HYPE:
            kept.append(row)
    kept.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
    return kept


def apply_price(rows: list[dict], price: float) -> None:
    for row in rows:
        row["price_usd"] = price
        row["usd"] = round(float(row.get("hype") or 0) * price, 2) if price else 0


def deposit_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if norm(str(r.get("to") or "")) == norm(DEPOSIT)]


def window_sum(rows: list[dict], end_day: str) -> float:
    end = parse_day(end_day).date()
    start = end - timedelta(days=HIST_DAYS - 1)
    total = 0.0
    for row in rows:
        try:
            day = parse_day(str(row.get("date") or "")).date()
        except ValueError:
            continue
        if start <= day <= end:
            total += float(row.get("hype") or 0)
    return total


def historical_hit(rows: list[dict]) -> dict | None:
    scoped = deposit_rows(rows)
    if not scoped:
        return None
    latest = max(str(r["date"]) for r in scoped)
    current = window_sum(scoped, latest)
    if current < HIST_MIN_HYPE:
        return None
    latest_d = parse_day(latest).date()
    prior_floor = latest_d - timedelta(days=HIST_DAYS)
    prior_max = 0.0
    prior_ends = {str(r["date"]) for r in scoped if parse_day(str(r["date"])).date() <= prior_floor}
    for end in prior_ends:
        prior_max = max(prior_max, window_sum(scoped, end))
    if prior_max <= 0 or current <= prior_max:
        return None
    return {
        "id": f"hist|{latest}|{round(current)}",
        "date": latest,
        "hype": current,
        "prior": prior_max,
    }


def news_event(day: str, label: str, group: list[dict]) -> str:
    total = sum(float(r["hype"]) for r in group)
    usd = sum(float(r.get("usd") or 0) for r in group)
    usd_text = fmt_usd_cn(usd)
    usd_clause = f"，按抓取时 Hyperliquid 中间价约合{usd_text}" if usd_text else ""
    return (
        f"据吴说数据中心统计，{fmt_date_cn(day)}，{SENDER_LABEL}的地址"
        f"向{label}转出 {fmt_hype(total)} 枚 HYPE{usd_clause}。"
        f"地址标注来自公开浏览器和公开监测，不是机构或 Coinbase 的官方确认，转入也不等于已经卖出。"
        f"\n{DASHBOARD_URL}"
    )


def news_hist(hit: dict, price: float) -> str:
    usd_text = fmt_usd_cn(hit["hype"] * price)
    usd_clause = f"，按抓取时中间价约合{usd_text}" if usd_text else ""
    return (
        f"{MARK_HIST}\n"
        f"据吴说数据中心统计，截至{fmt_date_cn(hit['date'])}，{SENDER_LABEL}的地址"
        f"近 {HIST_DAYS} 日向{DEPOSIT_LABEL}累计转出 {fmt_hype(hit['hype'])} 枚 HYPE{usd_clause}，"
        f"高于更早一段已记录的 {HIST_DAYS} 日窗口（约 {fmt_hype(hit['prior'])} 枚）。"
        f"标注不是官方确认，转入不等于已经卖出。"
        f"\n{DASHBOARD_URL}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="记已见，不推历史")
    parser.add_argument("--push", action="store_true", help="新的超阈值才推")
    args = parser.parse_args()
    if args.push and args.bootstrap:
        raise SystemExit("不要同时 --push 和 --bootstrap")
    if args.push and not STATE_PATH.exists():
        raise SystemExit("还没有 state.json。先运行 --bootstrap，避免把历史转账推出去。")

    fresh = fetch_transfers()
    rows = merge_rows(fresh, load_saved_rows())
    price = hype_mid()
    apply_price(rows, price)
    print(f"[multicoin-hype] fresh={len(fresh)} kept={len(rows)} price={price or 'none'}")

    state: dict[str, Any] = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    sent = {str(x).lower() for x in (state.get("sent") or [])}
    hist_sent = {str(x) for x in (state.get("hist_sent") or [])}

    webhook = (merge_env().get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (merge_env().get("LARK_ALERT_SECRET") or "").strip() or None
    pushed: list[str] = []

    alertable = [
        r
        for r in rows
        if str(r["tx"]).lower() not in sent
        and float(r["hype"]) >= LARK_MIN_HYPE
        and r.get("counterparty") != "系统地址"
    ]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in alertable:
        groups.setdefault((str(row["date"]), str(row["counterparty"])), []).append(row)

    for (day, label), group in groups.items():
        total = sum(float(r["hype"]) for r in group)
        if total < LARK_MIN_HYPE:
            continue
        text = news_event(day, label, group)
        print("---")
        print(text)
        if not args.push:
            continue
        if not webhook:
            print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
            raise SystemExit(2)
        send_lark(webhook, secret, text)
        pushed.append(f"{day}|{label}|{fmt_hype(total)}")
        for row in group:
            sent.add(str(row["tx"]).lower())
        time.sleep(0.4)

    hit = historical_hit(rows)
    if hit and hit["id"] not in hist_sent:
        text = news_hist(hit, price)
        print("---")
        print(text)
        if args.push:
            if not webhook:
                print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
                raise SystemExit(2)
            send_lark(webhook, secret, text)
            pushed.append(hit["id"])
            hist_sent.add(hit["id"])

    if args.bootstrap:
        for row in rows:
            sent.add(str(row["tx"]).lower())
        if hit:
            hist_sent.add(hit["id"])
        else:
            scoped = deposit_rows(rows)
            if scoped:
                latest = max(str(r["date"]) for r in scoped)
                hist_sent.add(f"boot|{latest}|{round(window_sum(scoped, latest))}")
        print(f"[multicoin-hype] bootstrap seen={len(sent)} hist={len(hist_sent)} 不推送")

    trailing = 0.0
    scoped = deposit_rows(rows)
    if scoped:
        trailing = window_sum(scoped, max(str(r["date"]) for r in scoped))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "code": "0",
        "updated_at": now_iso(),
        "page_min_hype": PAGE_MIN_HYPE,
        "lark_min_hype": LARK_MIN_HYPE,
        "hist_days": HIST_DAYS,
        "hist_min_hype": HIST_MIN_HYPE,
        "price_usd": price,
        "trailing_28d_hype": round(trailing, 4),
        "sender": SENDER,
        "sender_label": SENDER_LABEL,
        "deposit": DEPOSIT,
        "deposit_label": DEPOSIT_LABEL,
        "note": "地址标注来自公开浏览器和公开监测，不是 Multicoin 或 Coinbase 的官方确认。转入不等于已经卖出。",
        "rows": rows,
    }
    TRANSFERS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "sent": sorted(sent),
                "hist_sent": sorted(hist_sent),
                "updated_at": now_iso(),
                "pushed": pushed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[multicoin-hype] trailing_28d={trailing:.1f} pushed={pushed or 'none'}")


if __name__ == "__main__":
    main()
