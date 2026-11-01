# -*- coding: utf-8 -*-
"""UNI 日销毁 + 当日 Robinhood Chain DEX → Lark。

网页已有：other-indicators 的按链销毁，robinhood-chain 的全链 DEX。
不含 Uniswap 占比（网页没有这项）。

日常只在销毁最新日距今不超过 2 天时发【实时日常报告】。
更旧的日子只走【历史阈值警报】；回扫再加【特殊警报】。
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BURN_CANDIDATES = [
    HERE / "output" / "json" / "daily_by_chain.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "uni-burn" / "daily_by_chain.json",
    Path("/root/dashboard/public/json/uni-burn/daily_by_chain.json"),
]
DEX_CANDIDATES = [
    ROOT / "robinhood-chain" / "output" / "json" / "dex_volume.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "robinhood-chain" / "dex_volume.json",
    Path("/root/dashboard/public/json/robinhood-chain/dex_volume.json"),
]
STATE_PATH = HERE / "output" / "uni_burn_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD = "https://data.wublock123.com/dashboard/burns/uni"
FRESH_DAYS = 2
USD_LINE = 1_000_000.0
UNI_LINE = 180_000.0
RH_UNI_LINE = 150_000.0
DEX_LINE = 3_000_000_000.0


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


def load_rows(candidates: list[Path]) -> list[dict[str, Any]]:
    for path in candidates:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def day_of(raw: Any) -> str:
    return str(raw or "")[:10]


def burn_by_day(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        day = day_of(row.get("day"))
        if len(day) != 10:
            continue
        slot = out.setdefault(day, {"uni": 0.0, "usd": 0.0, "rh_uni": 0.0})
        uni = float(row.get("total_uni_burned") or 0)
        usd = float(row.get("total_uni_burned_usd") or 0)
        slot["uni"] += uni
        slot["usd"] += usd
        if str(row.get("blockchain") or "") == "robinhood":
            slot["rh_uni"] += uni
    return out


def dex_by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        day = day_of(row.get("day"))
        if len(day) != 10:
            continue
        val = row.get("dex_volume_usd")
        if isinstance(val, (int, float)):
            out[day] = float(val)
    return out


def fmt_uni(n: float) -> str:
    return f"{n / 1e4:.1f} 万枚"


def fmt_usd(n: float) -> str:
    a = abs(n)
    if a >= 1e8:
        return f"{a / 1e8:.2f} 亿美元"
    return f"{a / 1e4:.0f} 万美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def is_fresh(day: str) -> bool:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    return (today - d).days <= FRESH_DAYS


def reasons(stats: dict[str, float], dex: float | None) -> list[str]:
    out: list[str] = []
    if stats["usd"] >= USD_LINE:
        out.append(f"销毁价值 {fmt_usd(stats['usd'])}，超过 100 万美元")
    if stats["uni"] >= UNI_LINE:
        out.append(f"销毁 {fmt_uni(stats['uni'])}，超过 18 万枚")
    if stats["rh_uni"] >= RH_UNI_LINE:
        out.append(f"Robinhood Chain 销毁 {fmt_uni(stats['rh_uni'])}，超过 15 万枚")
    if dex is not None and dex >= DEX_LINE:
        out.append(f"Robinhood Chain DEX 成交 {fmt_usd(dex)}，超过 30 亿美元")
    return out


def body(day: str, stats: dict[str, float], dex: float | None) -> str:
    text = (
        f"吴说获悉，据吴说数据中心统计，{cn_day(day)} UNI 销毁约 {fmt_uni(stats['uni'])}，"
        f"价值约 {fmt_usd(stats['usd'])}。"
    )
    if stats["rh_uni"] >= 1:
        text += f"其中 Robinhood Chain 约 {fmt_uni(stats['rh_uni'])}。"
    if dex is not None:
        text += f"当日 Robinhood Chain DEX 成交约 {fmt_usd(dex)}。"
    return text


def build_daily(day: str, stats: dict[str, float], dex: float | None) -> str:
    return f"{LARK_MARK_DAILY}\nUNI 销毁：{cn_day(day)}\n{body(day, stats, dex)}\n{DASHBOARD}"


def build_threshold(day: str, stats: dict[str, float], dex: float | None, why: list[str], *, historical: bool) -> str:
    marks = LARK_MARK_THRESHOLD
    if historical:
        marks = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    text = (
        f"{marks}\n"
        "不是日常新闻，是历史阈值警报。\n"
        f"{body(day, stats, dex)}"
        f"触发原因：{'；'.join(why)}。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        text += f"本条为历史回扫发现（日期 {day}，发现时间 {found} UTC+8），请勿当成刚发生的一天。"
    return text + f"\n{DASHBOARD}"


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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
        code = result.get("code", result.get("StatusCode", 0))
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    burns = burn_by_day(load_rows(BURN_CANDIDATES))
    dexes = dex_by_day(load_rows(DEX_CANDIDATES))
    if not burns:
        print("[ERROR] 没有 UNI 销毁 JSON", file=sys.stderr)
        return 1
    latest = max(burns)
    fresh = is_fresh(latest)
    print(f"[uni-burn] latest={latest} fresh={fresh} dex_days={len(dexes)}")

    briefs: list[dict[str, Any]] = []
    if fresh:
        stats = burns[latest]
        dex = dexes.get(latest)
        briefs.append({
            "id": f"uni-burn-daily-{latest}",
            "text": build_daily(latest, stats, dex),
        })
        why = reasons(stats, dex)
        if why:
            briefs.append({
                "id": f"uni-burn-threshold-{latest}",
                "text": build_threshold(latest, stats, dex, why, historical=False),
            })
    else:
        print(f"[uni-burn] 销毁停在 {latest}，不发日常")

    if args.history_scan:
        for day in sorted(burns):
            if fresh and day == latest:
                continue
            stats = burns[day]
            dex = dexes.get(day)
            why = reasons(stats, dex)
            if not why:
                continue
            briefs.append({
                "id": f"uni-burn-threshold-{day}",
                "text": build_threshold(day, stats, dex, why, historical=True),
            })

    state = load_state()
    for brief in briefs:
        print("---")
        print(brief["id"], "already", brief["id"] in state["sentIds"])
        print(brief["text"])
    if not briefs:
        print("[skip] 没有可发内容")
        return 0
    if not args.push:
        print("[dry-run] 未推送")
        return 0

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        send_lark(webhook, secret, brief["text"])
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        save_state(state)
        print(f"[pushed] {brief['id']}")
        time.sleep(0.4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
