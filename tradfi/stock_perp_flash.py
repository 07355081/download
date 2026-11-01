# -*- coding: utf-8 -*-
"""股票类永续成交 → Lark 新闻体。只发最新已闭合交易日，且须命中其一：

- 极值：成交高于此前每一个交易日
- 首次：首次达到 50 亿美元整数关口
- 历史分位：分位刚升到 95% 及以上（前一交易日还没到）
- 异常变化：较前一交易日绝对值 ≥ 40%

不回扫历史，不发例行日报，不用警报抬头。正文一段，后接页面和图表地址。

用法：
  python stock_perp_flash.py
  python stock_perp_flash.py --push
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_PATH = HERE / "output" / "stock_perp_lark.json"
LOG_PATH = HERE / "stock_perp_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
OVERVIEW_URL = (
    "http://127.0.0.1:3000/api/coinglass/futures/tradfi-volume-overview"
    "?source=tag&interval=1d&limit=180&label=Stocks"
)
DAY_MS = 86_400_000
MAJOR_PCT = 40.0
PCTL_LINE = 95.0
GATE_USD = 50 * 1e8
MIN_PCTL_PRIOR = 20
FRESH_DAYS = 3
AVG_DAYS = 5
MIN_COVERAGE = 0.8
LOOKBACK = 14
NAMED = ("Binance", "Hyperliquid")
ALIASES = {"SKHX": "SKHYNIX", "SMSN": "SAMSUNG"}
NON_STOCK = {
    "SOXL", "SOXS", "KORU", "EWY", "QQQX", "DRAM", "XYZ100",
    "SKHY", "SNXX", "MUU", "MVLL", "SPCX", "ANTHROPIC", "UNITREE", "CXMT",
}
NAMES = {
    "SNDK": "闪迪", "SKHYNIX": "SK 海力士", "MU": "美光", "SAMSUNG": "三星电子",
    "MRVL": "迈威尔", "INTC": "英特尔", "NVDA": "英伟达", "TSLA": "特斯拉",
    "GOOGL": "谷歌", "AAPL": "苹果", "MSFT": "微软", "AMZN": "亚马逊",
    "ORCL": "甲骨文", "AVGO": "博通", "AMD": "AMD", "WDC": "西部数据",
    "CRCL": "Circle", "MSTR": "Strategy", "COIN": "Coinbase", "HOOD": "Robinhood",
}
PAGE = "https://data.wublock123.com/dashboard/tradfi"
CHART = "https://data.wublock123.com/dashboard/tradfi?label=Stocks"


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

def load_env_file(path: Path) -> dict[str, str]:
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


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def canonical(symbol: str) -> str:
    upper = symbol.strip().upper()
    return ALIASES.get(upper, upper)


def day_ms(ms: int) -> int:
    return int(ms) // DAY_MS * DAY_MS


def day_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def fmt_yi(n: float) -> str:
    return f"{n / 1e8:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    return f"{'+' if n > 0 else ''}{n:.1f}%"


def fetch_overview() -> dict[str, Any]:
    req = urllib.request.Request(OVERVIEW_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"overview 无 data: {str(doc)[:200]}")
    return data


def bucket(series: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for item in series:
        name = str(item.get("name") or "").strip()
        if not name or name == "Other":
            continue
        for pt in item.get("points") or []:
            ts = pt.get("time")
            val = pt.get("value")
            if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)) or val <= 0:
                continue
            day = day_ms(int(ts))
            row = out.setdefault(day, {})
            row[name] = row.get(name, 0.0) + float(val)
    return out


def sessions_of(by_venue: dict[int, dict[str, float]], now_ms: int) -> list[int]:
    closed = sorted(day for day in by_venue if now_ms >= day + DAY_MS)
    look = closed[-LOOKBACK:]
    best = max((len(by_venue[d]) for d in look), default=0)
    need = max(2, int(best * MIN_COVERAGE + 0.999))
    out = []
    for day in closed:
        if datetime.fromtimestamp(day / 1000, tz=timezone.utc).weekday() > 4:
            continue
        if len(by_venue[day]) >= need:
            out.append(day)
    return out


def stocks_on(by_symbol: dict[int, dict[str, float]], day: int) -> list[tuple[str, float]]:
    merged: dict[str, float] = {}
    for name, val in by_symbol.get(day, {}).items():
        sym = canonical(name)
        if sym in NON_STOCK:
            continue
        merged[sym] = merged.get(sym, 0.0) + val
    return sorted(merged.items(), key=lambda kv: -kv[1])[:3]


def view_of(
    by_venue: dict[int, dict[str, float]],
    by_symbol: dict[int, dict[str, float]],
    sessions: list[int],
    day: int,
) -> dict[str, Any]:
    idx = sessions.index(day)
    prev = sessions[idx - 1] if idx else None
    total = sum(by_venue[day].values())
    dod = None
    prev_total = None
    if prev is not None:
        prev_total = sum(by_venue[prev].values())
        if prev_total > 0 and day - prev <= 4 * DAY_MS:
            dod = (total - prev_total) / prev_total * 100.0
    window = sessions[max(0, idx - AVG_DAYS + 1) : idx + 1]
    avg = sum(sum(by_venue[d].values()) for d in window) / len(window)
    named = [(n, by_venue[day][n]) for n in NAMED if by_venue[day].get(n, 0) > 0]
    return {
        "day": day_str(day),
        "venues": len(by_venue[day]),
        "total": total,
        "prev": day_str(prev) if prev is not None else None,
        "prev_total": prev_total,
        "dod": dod,
        "avg_n": len(window),
        "avg": avg,
        "named": named,
        "top": stocks_on(by_symbol, day),
    }


def label(symbol: str) -> str:
    name = NAMES.get(symbol)
    return f"{name}（{symbol}）" if name else symbol


def cn_md(day: str) -> str:
    _year, month, date = day.split("-")
    return f"{int(month)}月{int(date)}日"


def classify(totals: list[float], idx: int, dod: float | None) -> dict[str, Any]:
    today = totals[idx]
    prior = totals[:idx]
    ath = bool(prior) and today > max(prior)
    pctl = None
    pctl_cross = False
    if len(prior) >= MIN_PCTL_PRIOR:
        pctl = sum(value <= today for value in prior) / len(prior) * 100.0
        prev_pctl = None
        if idx >= MIN_PCTL_PRIOR + 1:
            prev_prior = totals[: idx - 1]
            prev_val = totals[idx - 1]
            prev_pctl = sum(value <= prev_val for value in prev_prior) / len(prev_prior) * 100.0
        pctl_cross = pctl >= PCTL_LINE and not ath and (prev_pctl is None or prev_pctl < PCTL_LINE)
    gate = None
    if prior:
        level = int(today // GATE_USD) * GATE_USD
        if level >= GATE_USD and max(prior) < level <= today:
            gate = level
    move = isinstance(dod, float) and abs(dod) >= MAJOR_PCT
    return {
        "ath": ath,
        "pctl": pctl,
        "pctl_cross": pctl_cross,
        "gate": gate,
        "move": move,
        "fire": ath or pctl_cross or gate is not None or move,
    }


def build_news(view: dict[str, Any], trig: dict[str, Any]) -> str:
    day = cn_md(str(view["day"]))
    dod = view["dod"]
    lead_pct = f"，环比 {fmt_pct(float(dod))}" if isinstance(dod, float) else ""
    versus = ""
    prev_total = view.get("prev_total")
    if isinstance(prev_total, float) and view.get("prev"):
        delta = float(view["total"]) - prev_total
        word = "多" if delta >= 0 else "少"
        versus = f"较前一交易日（{cn_md(str(view['prev']))}）{word} {fmt_yi(abs(delta))}"
        if isinstance(dod, float):
            versus += f"（环比 {fmt_pct(dod)}）"
        versus += "，"
    named = "；".join(
        f"{name}成交 {fmt_yi(value)}，占 {value / view['total'] * 100:.1f}%"
        for name, value in view["named"]
    )
    named_s = f"{named}。" if named else ""
    top = view["top"]
    if len(top) == 3:
        bits = "、".join(f"{label(symbol)} {fmt_yi(value)}" for symbol, value in top)
        top_s = f"成交最多的三只股票为{bits}。"
    elif top:
        bits = "、".join(f"{label(symbol)} {fmt_yi(value)}" for symbol, value in top)
        top_s = f"成交靠前的个股为{bits}。"
    else:
        top_s = ""
    why: list[str] = []
    if trig["ath"]:
        why.append("该日成交创有统计以来新高")
    if trig["gate"]:
        why.append(f"为有统计以来首次达到 {fmt_yi(float(trig['gate']))}")
    if trig["pctl_cross"] and isinstance(trig["pctl"], float):
        why.append(f"历史分位升至 {trig['pctl']:.0f}%")
    if trig["move"] and isinstance(dod, float):
        why.append(f"单日环比变动达到 {fmt_pct(dod)}")
    why_s = f"{'，'.join(why)}。" if why else ""
    paragraph = (
        f"{day}股票永续合约交易量达 {fmt_yi(view['total'])}{lead_pct}。"
        f"据吴说数据中心统计，当日股票类永续合约合计成交 {fmt_yi(view['total'])}，"
        f"{versus}近 {view['avg_n']} 个交易日均值 {fmt_yi(view['avg'])}。"
        f"{named_s}{top_s}{why_s}"
        "个股不含 ETF 与未上市公司永续。"
    )
    return f"{paragraph}\n{PAGE}\n{CHART}"


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if raw and str(json.loads(raw).get("code", "0")) != "0":
        raise RuntimeError(raw)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true", help="已停用，保留参数以免旧定时报错")
    args = parser.parse_args()
    if args.history_scan:
        print("[stock-perp] 不回扫历史")
    try:
        data = fetch_overview()
        by_venue = bucket(data.get("exchanges") or [])
        by_symbol = bucket(data.get("symbols") or [])
        now_ms = int(time.time() * 1000)
        sessions = sessions_of(by_venue, now_ms)
        if len(sessions) < 2:
            raise RuntimeError("可用交易日不足")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    latest_day = sessions[-1]
    latest = view_of(by_venue, by_symbol, sessions, latest_day)
    age = (datetime.now(timezone.utc).date() - datetime.strptime(latest["day"], "%Y-%m-%d").date()).days
    totals = [sum(by_venue[day].values()) for day in sessions]
    trig = classify(totals, len(sessions) - 1, latest["dod"] if isinstance(latest["dod"], float) else None)
    print(
        f"[stock-perp] latest={latest['day']} total={latest['total']:.0f} dod={latest['dod']} "
        f"ath={trig['ath']} pctl={trig['pctl']} cross={trig['pctl_cross']} gate={trig['gate']} move={trig['move']}"
    )
    if age > FRESH_DAYS:
        print(f"[skip] {latest['day']} 过旧 age={age}")
        return 0
    if not trig["fire"]:
        print("[skip] 未出现新高、首次过关、分位升至 95% 或单日大变动")
        return 0
    text = build_news(latest, trig)
    msg_id = f"stock-perp-news-{latest['day']}"
    print("---")
    print(msg_id)
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = merge_env()
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
    state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
