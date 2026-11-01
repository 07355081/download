"""实时流水账：只写刚拉到的盘中数字，过时的序列整段不写。

历史级数据（ETF 大额、月成交、3σ、GMGN 日环比、Papertrade 大幅变动）仍走各自的阈值推送，不进这篇。

用法：
  python realtime_ledger.py            # 只打印
  python realtime_ledger.py --push     # 发 Lark；同一上海日同一小时只发一次
  python realtime_ledger.py --push --force
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_ROOT = Path(os.environ.get("DASHBOARD_JSON") or "/root/dashboard/public/json")
LOG_PATH = HERE / "realtime_ledger.log"
API = os.environ.get("LEDGER_API_BASE") or "https://data.wublock123.com"
DASHBOARD_URL = os.environ.get("LEDGER_DASHBOARD_URL") or f"{API.rstrip('/')}/dashboard"
MAX_AGE = timedelta(minutes=90)
TZ_CN = timezone(timedelta(hours=8))
ENV_FILES = [
    HERE / ".env",
    Path("/root/dashboard/.env.live"),
    HERE.parent / "data-dashboard" / ".env.live",
]


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

def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in ENV_FILES:
        merged.update(load_env(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "wu-realtime-ledger/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_usd(text: str) -> float | None:
    raw = text.strip().replace("$", "").replace(",", "")
    mult = 1.0
    if raw.endswith("T"):
        mult, raw = 1e12, raw[:-1]
    elif raw.endswith("B"):
        mult, raw = 1e9, raw[:-1]
    elif raw.endswith("M"):
        mult, raw = 1e6, raw[:-1]
    elif raw.endswith("K"):
        mult, raw = 1e3, raw[:-1]
    try:
        return float(raw) * mult
    except ValueError:
        return None


def parse_pct(text: str) -> float | None:
    raw = text.strip().replace("%", "").replace("+", "")
    try:
        return float(raw)
    except ValueError:
        return None


def fmt_usd(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:,.0f} 万美元"
    return f"{value:,.0f} 美元"


def fmt_price(value: float) -> str:
    if value >= 10000:
        return f"{value / 10000:.2f} 万美元"
    if value >= 100:
        return f"{value:,.0f} 美元"
    return f"{value:,.2f} 美元"


def fmt_chg(pct: float) -> str:
    if pct >= 0:
        return f"上涨 {pct:.2f}%"
    return f"下跌 {abs(pct):.2f}%"


def rows_of(module: str) -> list[dict[str, Any]]:
    payload = http_json(f"{API}/api/module-top/{module}")
    if not payload.get("ok"):
        raise RuntimeError(f"{module} 接口未返回 ok")
    rows = ((payload.get("view") or {}).get("rows")) or []
    if not rows:
        raise RuntimeError(f"{module} 没有行")
    return rows


def cell_texts(row: dict[str, Any]) -> list[str]:
    return [str(c.get("text") or "") for c in (row.get("cells") or [])]


def find_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("symbol") or "").upper() == symbol:
            return row
    return None


def age_of(dt: datetime) -> timedelta:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CN)
    return datetime.now(TZ_CN) - dt.astimezone(TZ_CN)


def papertrade_clause() -> str | None:
    path = JSON_ROOT / "hyperliquid" / "papertrade_stats.json"
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return None
    as_of = str(data.get("as_of") or "")
    try:
        stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return None
    if age_of(stamp) > MAX_AGE:
        return None
    snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
    tvl = float(snap.get("tvl_usd") or 0)
    if tvl <= 0:
        return None
    return f"HyperEVM 合成永续 Papertrade 的 TVL 约 {fmt_usd(tvl)}"


def gmgn_clause() -> str | None:
    path = JSON_ROOT / "gmgn" / "meme_markets_24h.json"
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = str(doc.get("fetched_at_cn") or "")
    try:
        stamp = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_CN)
    except ValueError:
        return None
    if age_of(stamp) > MAX_AGE:
        return None
    total = float(doc.get("total_volume") or 0)
    if total <= 0:
        return None
    chains = [c for c in (doc.get("chains") or []) if isinstance(c, dict)]
    labels = [str(c.get("label") or c.get("chain") or "?") for c in chains]
    # 流水账顺序与 flash 正文一致
    prefer = ["Robinhood", "Solana", "BNB", "Arc", "以太坊"]
    ordered_labels: list[str] = []
    for lab in prefer:
        if lab in labels:
            ordered_labels.append(lab)
    for lab in labels:
        if lab not in ordered_labels:
            ordered_labels.append(lab)
    names = "、".join(ordered_labels) if ordered_labels else "Solana、Robinhood、BNB、Arc"
    return f"{names} 等主要 Meme 市场 24 小时核心成交约 {fmt_usd(total)}"


def global_liq_clause() -> str | None:
    """Optional CoinGlass global 24h liquidation (All). Fail soft."""
    conf = env()
    api_key = (conf.get("COINGLASS_API_KEY") or "").strip()
    if not api_key:
        common = HERE / "coinglass-history" / "_common.py"
        if common.is_file():
            for line in common.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("COINGLASS_API_KEY"):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        return None
    url = "https://open-api-v4.coinglass.com/api/futures/liquidation/exchange-list?range=24h"
    try:
        req = urllib.request.Request(
            url,
            headers={"CG-API-KEY": api_key, "User-Agent": "wu-realtime-ledger/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if str(payload.get("code")) not in ("0", "0.0") and payload.get("code") != 0:
        return None
    for row in payload.get("data") or []:
        if str(row.get("exchange") or "").lower() != "all":
            continue
        total = float(row.get("liquidation_usd") or 0)
        long_usd = float(row.get("longLiquidation_usd") or 0)
        short_usd = float(row.get("shortLiquidation_usd") or 0)
        if total <= 0:
            return None
        side = "主爆多单" if long_usd >= short_usd else "主爆空单"
        main = long_usd if long_usd >= short_usd else short_usd
        side_label = "多单" if long_usd >= short_usd else "空单"
        return f"全球爆仓约 {fmt_usd(total)}，{side}，{side_label}约 {fmt_usd(main)}"
    return None


def build_text() -> str:
    futures = rows_of("futures")
    liqs = rows_of("liquidations")
    btc = find_row(futures, "BTC")
    eth = find_row(futures, "ETH")
    sol = find_row(futures, "SOL")
    if not btc or not eth:
        raise RuntimeError("实时持仓榜缺少 BTC 或 ETH，不发过时稿")
    btc_c, eth_c = cell_texts(btc), cell_texts(eth)
    if len(btc_c) < 3 or len(eth_c) < 3:
        raise RuntimeError("实时持仓榜字段不完整")
    btc_px, eth_px = parse_usd(btc_c[0]), parse_usd(eth_c[0])
    btc_chg, eth_chg = parse_pct(btc_c[1]), parse_pct(eth_c[1])
    btc_oi, eth_oi = parse_usd(btc_c[2]), parse_usd(eth_c[2])
    if None in (btc_px, eth_px, btc_chg, eth_chg, btc_oi, eth_oi):
        raise RuntimeError("实时价格解析失败")

    now = datetime.now(TZ_CN)
    stamp = f"{now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    parts = [
        f"据吴说数据中心实时数据，截至 {stamp}，"
        f"比特币报 {fmt_price(btc_px)}，24 小时{fmt_chg(btc_chg)}，合约持仓约 {fmt_usd(btc_oi)}；"
        f"以太坊报 {fmt_price(eth_px)}，24 小时{fmt_chg(eth_chg)}，合约持仓约 {fmt_usd(eth_oi)}"
    ]
    if sol:
        sol_c = cell_texts(sol)
        if len(sol_c) >= 2:
            sol_px, sol_chg = parse_usd(sol_c[0]), parse_pct(sol_c[1])
            if sol_px is not None and sol_chg is not None:
                parts.append(f"；SOL 报 {fmt_price(sol_px)}，24 小时{fmt_chg(sol_chg)}")
    parts.append("。")

    liq_bits = []
    for symbol, label in (("BTC", "比特币"), ("ETH", "以太坊"), ("SOL", "SOL")):
        row = find_row(liqs, symbol)
        if not row:
            continue
        texts = cell_texts(row)
        amount = parse_usd(texts[0]) if texts else None
        if amount:
            liq_bits.append(f"{label}约 {fmt_usd(amount)}")
    global_liq = global_liq_clause()
    if global_liq:
        parts.append(f"近 24 小时{global_liq}")
        if liq_bits:
            parts.append("；其中" + "，".join(liq_bits) + "。")
        else:
            parts.append("。")
    elif len(liq_bits) >= 2:
        parts.append("近 24 小时爆仓额，" + "，".join(liq_bits) + "。")

    extras = [c for c in (papertrade_clause(), gmgn_clause()) if c]
    if extras:
        parts.append("另据近 90 分钟内更新的快照，" + "；".join(extras) + "。")
    parts.append(f"\n{DASHBOARD_URL}")
    text = "".join(parts)
    mark = "【实时日常报告】"
    if not text.startswith(mark):
        text = f"{mark}\n{text}"
    return text


def already_pushed(day: str, slot: str) -> bool:
    if not LOG_PATH.exists():
        return False
    return f"day={day} slot={slot} ok=True" in LOG_PATH.read_text(encoding="utf-8", errors="replace")


def send_lark(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        text = build_text()
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[skip] 实时数据不可用，不发例行连推：{exc}", file=sys.stderr)
        return 1

    print(text)
    now = datetime.now(TZ_CN)
    day = now.strftime("%Y-%m-%d")
    slot = now.strftime("%H")
    if not args.push:
        print(f"\n[dry-run] day={day} slot={slot}", file=sys.stderr)
        return 0
    if not args.force and already_pushed(day, slot):
        print(f"{day} {slot} 时已发过例行连推，跳过。", file=sys.stderr)
        return 0

    conf = env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (conf.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    try:
        result = send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[ERROR] Lark 发送失败: {exc}", file=sys.stderr)
        return 1
    ok = str(result.get("code", "0")) == "0"
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {now.strftime('%Y-%m-%d %H:%M:%S')} day={day} slot={slot} ok={ok} resp={result}\n{text}\n\n")
    if not ok:
        print(f"[ERROR] 推送失败: {result}", file=sys.stderr)
        return 1
    print("\n[pushed]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
