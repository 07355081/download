# -*- coding: utf-8 -*-
"""自有口径周报 → Lark（不是 Lookonchain 同源）。

拼装：
  - 稳定币总市值：周日相对上个周日（DefiLlama）
  - DEX 现货：自然周成交合计环比（DefiLlama）
  - 永续：CEX 合约日成交周合计环比（cex-asset&vol 派生）
  - 协议收入：DefiLlama dailyRevenue 周合计环比
  - Bitmine：当周 ETH 增减（有则写）

- 日常：最近一个已结束 UTC 周（周日截止）一条【实时日常报告】
- 阈值：稳定币 |Δ|≥5000 万美元，或 DEX/永续 |WoW|≥15%，或协议收入 |WoW|≥10%，
  或 Bitmine 当周 |ΔETH|≥2 万 →【历史阈值警报】+【特殊警报】
- --history-scan：近若干周里过线且未发过的，标明历史回扫

用法：
  python flash.py
  python flash.py --push --history-scan
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_PATH = HERE / "output" / "lookonchain_week_lark.json"
LOG_PATH = HERE / "lookonchain_week_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/lookonchain-week"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
HISTORY_WEEKS = 8

STABLE_USD = 50_000_000.0
DEX_WOW = 15.0
PERP_WOW = 15.0
REV_WOW = 10.0
BITMINE_ETH = 20_000.0


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


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def first_file(cands: list[Path]) -> Path | None:
    for p in cands:
        if p.is_file():
            return p
    return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def week_end_sunday(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()))


def cn_span(week_end: str) -> str:
    end = date.fromisoformat(week_end)
    start = end - timedelta(days=6)
    if start.year == end.year:
        return (
            f"{start.year} 年 {start.month} 月 {start.day} 日至 "
            f"{end.month} 月 {end.day} 日"
        )
    return (
        f"{start.year} 年 {start.month} 月 {start.day} 日至 "
        f"{end.year} 年 {end.month} 月 {end.day} 日"
    )


def fmt_usd(n: float) -> str:
    a = abs(n)
    if a >= 1e8:
        return f"{a / 1e8:.2f} 亿美元"
    if a >= 1e4:
        return f"{a / 1e4:.0f} 万美元"
    return f"{a:,.0f} 美元"


def fmt_pct(n: float | None) -> str:
    if n is None:
        return "无上期"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.2f}%"


def verb_usd(n: float) -> str:
    return "增长" if n >= 0 else "下降"


def wow(curr: float, prev: float | None) -> float | None:
    if prev is None or prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def series_from_rows(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")
        val = row.get(key)
        if len(day) == 10 and isinstance(val, (int, float)):
            out[day] = float(val)
    return out


def sum_week(day_map: dict[str, float], week_end: str) -> float | None:
    end = date.fromisoformat(week_end)
    start = end - timedelta(days=6)
    total = 0.0
    n = 0
    d = start
    while d <= end:
        v = day_map.get(d.isoformat())
        if v is not None:
            total += v
            n += 1
        d += timedelta(days=1)
    if n < 5:
        return None
    return total


def level_on_or_before(day_map: dict[str, float], day: str) -> float | None:
    hit = None
    for k in sorted(day_map):
        if k <= day:
            hit = day_map[k]
        else:
            break
    return hit


def complete_week_ends(day_maps: list[dict[str, float]], today: date) -> list[str]:
    days: set[str] = set()
    for m in day_maps:
        days.update(m)
    ends = {week_end_sunday(date.fromisoformat(d)).isoformat() for d in days}
    # 只保留已结束的周（周日 < 今天）
    return sorted(w for w in ends if date.fromisoformat(w) < today)


def load_sources() -> dict[str, Any]:
    dash = Path("/root/dashboard/public/json")
    local_dash = ROOT.parent / "data-dashboard" / "public" / "json"
    stable_path = first_file(
        [
            ROOT / "stablecoin" / "output" / "json" / "stablecoins_marketcap.json",
            local_dash / "stablecoin" / "stablecoins_marketcap.json",
            dash / "stablecoin" / "stablecoins_marketcap.json",
        ]
    )
    dex_path = first_file(
        [
            ROOT / "dex-volume" / "output" / "json" / "dex_volume.json",
            local_dash / "dex-volume" / "dex_volume.json",
            dash / "dex-volume" / "dex_volume.json",
        ]
    )
    perp_path = first_file(
        [
            ROOT / "perp-volume" / "output" / "json" / "perp_volume.json",
            local_dash / "perp-volume" / "perp_volume.json",
            dash / "perp-volume" / "perp_volume.json",
        ]
    )
    rev_path = first_file(
        [
            ROOT / "protocol-revenue-daily" / "output" / "json" / "protocol_revenue_daily.json",
            local_dash / "protocol-revenue-daily" / "protocol_revenue_daily.json",
            dash / "protocol-revenue-daily" / "protocol_revenue_daily.json",
        ]
    )
    bit_path = first_file(
        [
            ROOT / "bitmine-eth" / "output" / "json" / "bitmine_eth.json",
            local_dash / "bitmine-eth" / "bitmine_eth.json",
            dash / "bitmine-eth" / "bitmine_eth.json",
        ]
    )
    missing = [
        name
        for name, path in (
            ("stablecoin", stable_path),
            ("dex", dex_path),
            ("perp", perp_path),
            ("revenue", rev_path),
        )
        if path is None
    ]
    if missing:
        raise FileNotFoundError("缺少数据文件: " + ", ".join(missing))

    stable_doc = load_json(stable_path)  # type: ignore[arg-type]
    dex_doc = load_json(dex_path)  # type: ignore[arg-type]
    perp_doc = load_json(perp_path)  # type: ignore[arg-type]
    rev_doc = load_json(rev_path)  # type: ignore[arg-type]
    bit_doc = load_json(bit_path) if bit_path else {}

    stable_rows = stable_doc.get("data") if isinstance(stable_doc, dict) else []
    dex_rows = dex_doc.get("data") if isinstance(dex_doc, dict) else []
    perp_rows = perp_doc.get("data") if isinstance(perp_doc, dict) else []
    rev_rows = rev_doc.get("data") if isinstance(rev_doc, dict) else []
    return {
        "stable": series_from_rows(stable_rows if isinstance(stable_rows, list) else [], "all_stablecoins"),
        "dex": series_from_rows(dex_rows if isinstance(dex_rows, list) else [], "global"),
        "perp": series_from_rows(perp_rows if isinstance(perp_rows, list) else [], "global"),
        "revenue": series_from_rows(rev_rows if isinstance(rev_rows, list) else [], "revenue_usd"),
        "bitmine": bit_doc.get("history") if isinstance(bit_doc, dict) else [],
    }


def bitmine_add(history: Any, week_end: str) -> int | None:
    if not isinstance(history, list):
        return None
    end = date.fromisoformat(week_end)
    start = end - timedelta(days=6)
    hit = None
    for row in history:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")
        if len(day) != 10:
            continue
        d = date.fromisoformat(day)
        if start <= d <= end and isinstance(row.get("week_add"), int):
            hit = int(row["week_add"])
    return hit


def snapshot_week(src: dict[str, Any], week_end: str, prev_end: str | None) -> dict[str, Any]:
    stable_now = level_on_or_before(src["stable"], week_end)
    stable_prev = level_on_or_before(src["stable"], prev_end) if prev_end else None
    stable_delta = None
    if stable_now is not None and stable_prev is not None:
        stable_delta = stable_now - stable_prev

    dex = sum_week(src["dex"], week_end)
    dex_prev = sum_week(src["dex"], prev_end) if prev_end else None
    perp = sum_week(src["perp"], week_end)
    perp_prev = sum_week(src["perp"], prev_end) if prev_end else None
    rev = sum_week(src["revenue"], week_end)
    rev_prev = sum_week(src["revenue"], prev_end) if prev_end else None
    return {
        "week_end": week_end,
        "stable_delta": stable_delta,
        "dex_wow": wow(dex, dex_prev) if dex is not None else None,
        "perp_wow": wow(perp, perp_prev) if perp is not None else None,
        "rev_wow": wow(rev, rev_prev) if rev is not None else None,
        "bitmine": bitmine_add(src["bitmine"], week_end),
        "has_core": stable_delta is not None or dex is not None or perp is not None or rev is not None,
    }


def threshold_reasons(row: dict[str, Any], lines: dict[str, float]) -> list[str]:
    out: list[str] = []
    sd = row.get("stable_delta")
    if isinstance(sd, float) and abs(sd) >= lines["stable"]:
        out.append(f"稳定币总市值{verb_usd(sd)} {fmt_usd(sd)}（线 ±{fmt_usd(lines['stable'])}）")
    for key, label, line_key in (
        ("dex_wow", "DEX 现货成交量环比", "dex"),
        ("perp_wow", "CEX 合约成交量环比", "perp"),
        ("rev_wow", "协议收入环比", "rev"),
    ):
        v = row.get(key)
        if isinstance(v, float) and abs(v) >= lines[line_key]:
            out.append(f"{label} {fmt_pct(v)}（线 ±{lines[line_key]:g}%）")
    bm = row.get("bitmine")
    if isinstance(bm, int) and abs(bm) >= lines["bitmine"]:
        out.append(f"Bitmine 当周 ETH 变动 {bm:+,} 枚（线 ±{lines['bitmine']:g}）")
    return out


def build_daily(row: dict[str, Any]) -> str:
    span = cn_span(str(row["week_end"]))
    bits = []
    sd = row.get("stable_delta")
    if isinstance(sd, float):
        bits.append(f"稳定币总市值{verb_usd(sd)} {fmt_usd(sd)}")
    if isinstance(row.get("dex_wow"), float):
        bits.append(f"DEX 现货成交量环比 {fmt_pct(row['dex_wow'])}")
    if isinstance(row.get("perp_wow"), float):
        bits.append(f"CEX 合约成交量环比 {fmt_pct(row['perp_wow'])}")
    if isinstance(row.get("rev_wow"), float):
        bits.append(f"协议收入环比 {fmt_pct(row['rev_wow'])}")
    bm = row.get("bitmine")
    if isinstance(bm, int):
        verb = "增持" if bm >= 0 else "减持"
        bits.append(f"Bitmine {verb} {abs(bm):,} 枚 ETH")
    body = (
        f"吴说获悉，据吴说数据中心统计（非 Lookonchain），{span}："
        + "；".join(bits)
        + "。"
        "稳定币与 DEX 现货、协议收入来自 DefiLlama；合约成交为 CEX 样本日成交周合计，"
        "不是 DefiLlama 衍生品全集。数字不宜与 Lookonchain 周报直接对表。"
    )
    return f"数据中心周报：{span}\n{body}\n{PAGE}"


def build_threshold(row: dict[str, Any], reasons: list[str], *, historical: bool) -> str:
    span = cn_span(str(row["week_end"]))
    body = (
        "不是日常新闻，是历史阈值警报。\n"
        f"吴说获悉，据吴说数据中心统计（非 Lookonchain），{span}周报出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        "本条为阈值异动提醒，不是每周例行简报。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += (
            f"本条为历史回扫发现（周结束日 {row['week_end']}，发现时间 {found} UTC+8），"
            "请勿当成刚结束的一周。"
        )
    return f"数据中心周报异动：{span}\n{body}\n{PAGE}"


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) not in ("0", "None"):
            code = result.get("code", result.get("StatusCode"))
            if code not in (0, "0", None):
                raise RuntimeError(f"Lark 返回异常: {result}")


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


def write_page(src: dict[str, Any]) -> None:
    specs = (
        ("stable", "稳定币市值", "Stablecoin mcap"),
        ("dex", "DEX 成交", "DEX volume"),
        ("perp", "合约成交", "Perp volume"),
        ("revenue", "协议收入", "Protocol revenue"),
    )
    series = []
    for key, label, label_en in specs:
        day_map = src.get(key)
        if not isinstance(day_map, dict):
            continue
        days = sorted(day for day in day_map if isinstance(day_map.get(day), (int, float)))[-120:]
        points = [{"month": day, "usd": float(day_map[day])} for day in days]
        if len(points) >= 2:
            series.append({"label": label, "labelEn": label_en, "months": points})
    if not series:
        return
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "吴说数据中心既有日序列",
        "series": series,
    }
    text = json.dumps(payload, ensure_ascii=False) + "\n"
    targets = [
        HERE / "output" / "json" / "lookonchain_week.json",
        Path("/root/dashboard/public/json/lookonchain-week/lookonchain_week.json"),
        ROOT.parent / "data-dashboard" / "public" / "json" / "lookonchain-week" / "lookonchain_week.json",
    ]
    for path in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            continue
    print(f"[lookonchain-week] page series={len(series)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    lines = {
        "stable": env_float(env, "LOOKONCHAIN_WEEK_STABLE_USD", STABLE_USD),
        "dex": env_float(env, "LOOKONCHAIN_WEEK_DEX_WOW", DEX_WOW),
        "perp": env_float(env, "LOOKONCHAIN_WEEK_PERP_WOW", PERP_WOW),
        "rev": env_float(env, "LOOKONCHAIN_WEEK_REV_WOW", REV_WOW),
        "bitmine": env_float(env, "LOOKONCHAIN_WEEK_BITMINE_ETH", BITMINE_ETH),
    }
    src = load_sources()
    write_page(src)
    today = datetime.now(TZ_CN).date()
    ends = complete_week_ends([src["stable"], src["dex"], src["perp"], src["revenue"]], today)
    if not ends:
        print("[ERROR] 没有已结束的完整周", file=sys.stderr)
        return 1

    rows: list[dict[str, Any]] = []
    prev = None
    for w in ends:
        row = snapshot_week(src, w, prev)
        if row["has_core"]:
            rows.append(row)
            prev = w
        else:
            prev = w

    if not rows:
        print("[ERROR] 周快照为空", file=sys.stderr)
        return 1

    latest = rows[-1]
    briefs: list[dict[str, Any]] = []

    def add_pair(row: dict[str, Any], *, historical: bool) -> None:
        why = threshold_reasons(row, lines)
        wid = str(row["week_end"])
        if why:
            briefs.append(
                {
                    "id": f"lookonchain-week-threshold-{wid}",
                    "kind": "threshold",
                    "historical": historical,
                    "text": build_threshold(row, why, historical=historical),
                }
            )
        if not historical:
            briefs.append(
                {
                    "id": f"lookonchain-week-daily-{wid}",
                    "kind": "daily",
                    "historical": False,
                    "text": build_daily(row),
                }
            )

    add_pair(latest, historical=False)
    if args.history_scan:
        for row in rows[-HISTORY_WEEKS:-1]:
            add_pair(row, historical=True)

    state = load_state()
    print(
        f"[lookonchain-week] latest={latest['week_end']} briefs={len(briefs)} "
        f"stable={latest.get('stable_delta')} dex={latest.get('dex_wow')} "
        f"perp={latest.get('perp_wow')} rev={latest.get('rev_wow')} bitmine={latest.get('bitmine')}"
    )
    for brief in briefs:
        mark = (
            f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
            if brief["kind"] == "threshold"
            else LARK_MARK_DAILY
        )
        already = brief["id"] in state["sentIds"]
        print("---")
        print(f"id={brief['id']} historical={brief['historical']} already={already}")
        print(f"{mark}\n{brief['text']}")

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    sent_any = False
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"skip {brief['id']}")
            continue
        mark = (
            f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
            if brief["kind"] == "threshold"
            else LARK_MARK_DAILY
        )
        text = f"{mark}\n{brief['text']}"
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-80:]
        save_state(state)
        print(f"[lookonchain-week] state saved → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
