# -*- coding: utf-8 -*-
"""Robinhood Chain RWA 日成交 / 市值 → Lark。

口径：Dune adam_tehc query 8071940（成交）、8073404（市值），与看板 RWA 页同一份 JSON。

- 日常：最近一个不太过期的完整 UTC 日【实时日常报告】（合计 + 股票现货 + Meme×股，附带商品/ETF 与市值）
- 阈值：成交合计 / Meme×股 / 股票现货，或市值合计，创此前序列新高，或历史分位 ≥85
  →【历史阈值警报】；创历史新高再加【特殊警报】
- --history-scan：回扫更早过线的日期，抬头加【特殊警报】并写明历史回扫

用法：
  python rwa_flash.py
  python rwa_flash.py --push --history-scan
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
JSON_DIR_CANDIDATES = [
    HERE / "output" / "json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "robinhood-chain",
    Path("/root/dashboard/public/json/robinhood-chain"),
]
STATE_PATH = HERE / "output" / "rwa_lark.json"
LOG_PATH = HERE / "rwa_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/robinhood-chain"

# 对方缓存最多约 7 天不动；再留几天，避免周更后第二天被当成过期。
FRESH_DAYS = 10
MIN_PRIOR = 14
PCTL_LINE = 85.0
MIN_VOL_ALERT = 5e7  # 成交阈值至少 5000 万美元，避免早期爬坡天天「新高」
MIN_AUM_ALERT = 2e7
HISTORY_LIMIT = 4
DEFAULT_HISTORY_DAYS = 90

VOL_LABELS = {
    "total": "成交合计",
    "stock": "股票现货",
    "meme": "Meme×股票",
    "commodity": "大宗商品",
    "etf": "ETF 与国债",
}
AUM_LABELS = {
    "total": "市值合计",
    "stock": "股票",
    "etf": "ETF",
    "commodity": "大宗商品",
    "treasury": "国债",
}
# 成交：新高或高分位都报。市值是存量、高位横着走，只报创新高，不报分位。
VOL_ALERT_KEYS = ("total", "meme", "stock")
AUM_ALERT_KEYS = ("total",)


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
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_json_dir() -> Path:
    for path in JSON_DIR_CANDIDATES:
        if (path / "rwa_volume.json").is_file() or (path / "rwa_aum.json").is_file():
            return path
    raise FileNotFoundError("找不到 robinhood-chain RWA JSON（rwa_volume / rwa_aum）")


def load_rows(directory: Path, name: str) -> list[dict[str, Any]]:
    path = directory / f"{name}.json"
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def day_key(row: dict[str, Any]) -> str | None:
    raw = row.get("block_date") or row.get("day") or row.get("time")
    day = str(raw or "")[:10]
    return day if len(day) == 10 and day[4] == "-" else None


def num(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def norm_name(raw: str) -> str:
    return raw.replace("\u00d7", "x").replace("×", "x").lower()


def classify_volume(raw: str) -> str | None:
    name = norm_name(raw)
    if "stock trading" in name:
        return "stock"
    if "memecoin" in name and "stock" in name:
        return "meme"
    if "commodit" in name:
        return "commodity"
    if "etf" in name or "treasur" in name:
        return "etf"
    return None


def classify_aum(raw: str) -> str | None:
    name = norm_name(raw)
    if name == "stock":
        return "stock"
    if name == "etf":
        return "etf"
    if "commodit" in name:
        return "commodity"
    if "treasur" in name:
        return "treasury"
    return None


def pivot(
    rows: list[dict[str, Any]],
    dim_key: str,
    value_key: str,
    classify,
) -> dict[str, dict[str, float]]:
    """day -> {series: usd, total: usd}"""
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        day = day_key(row)
        if not day:
            continue
        kind = classify(str(row.get(dim_key) or ""))
        if not kind:
            continue
        bucket = out.setdefault(day, {"total": 0.0})
        value = num(row, value_key)
        bucket[kind] = bucket.get(kind, 0.0) + value
        bucket["total"] += value
    return out


def series_of(table: dict[str, dict[str, float]], key: str) -> dict[str, float]:
    return {day: row.get(key, 0.0) for day, row in table.items()}


def cn_day(day: str) -> str:
    year, month, date = day.split("-")
    return f"{year} 年 {int(month)} 月 {int(date)} 日"


def usd_cn(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f} 万美元"
    return f"{value:.0f} 美元"


def signed_pct(value: float) -> str:
    return f"{value:+.1f}%"


def is_fresh(day: str) -> bool:
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    return 0 <= age <= FRESH_DAYS


def stats_vs_prior(series: dict[str, float], day: str, min_value: float) -> dict[str, Any] | None:
    if day not in series:
        return None
    value = series[day]
    if value < min_value:
        return None
    prior = [series[item] for item in series if item < day]
    if len(prior) < MIN_PRIOR:
        return None
    below = sum(1 for item in prior if item <= value)
    return {
        "value": value,
        "ath": value > max(prior),
        "pctl": below / len(prior) * 100.0,
        "prior_max": max(prior),
        "n_prior": len(prior),
    }


def evaluate_day(
    vol: dict[str, dict[str, float]],
    aum: dict[str, dict[str, float]],
    day: str,
) -> list[str]:
    reasons: list[str] = []
    for key in VOL_ALERT_KEYS:
        item = stats_vs_prior(series_of(vol, key), day, MIN_VOL_ALERT)
        if not item:
            continue
        label = VOL_LABELS[key]
        if item["ath"]:
            reasons.append(f"{label} {usd_cn(item['value'])} 创序列新高")
        elif item["pctl"] >= PCTL_LINE:
            reasons.append(
                f"{label} {usd_cn(item['value'])} 处历史分位 {item['pctl']:.0f}（阈值 {PCTL_LINE:.0f}）"
            )
    for key in AUM_ALERT_KEYS:
        item = stats_vs_prior(series_of(aum, key), day, MIN_AUM_ALERT)
        if item and item["ath"]:
            reasons.append(f"{AUM_LABELS[key]} {usd_cn(item['value'])} 创序列新高")
    return reasons


def split_bits(row: dict[str, float] | None, labels: dict[str, str], keys: tuple[str, ...]) -> str:
    if not row:
        return ""
    parts = [f"{labels[key]}约 {usd_cn(row.get(key, 0.0))}" for key in keys if row.get(key)]
    return "、".join(parts)


def build_daily(day: str, vol: dict[str, dict[str, float]], aum: dict[str, dict[str, float]]) -> str:
    row = vol.get(day) or {}
    total = row.get("total", 0.0)
    prev_days = sorted(item for item in vol if item < day)
    dod = ""
    if prev_days:
        prev = vol[prev_days[-1]].get("total", 0.0)
        if prev > 0:
            dod = f"较前一日（{prev_days[-1]}）成交环比 {signed_pct((total / prev - 1.0) * 100.0)}。"
    vol_bits = split_bits(row, VOL_LABELS, ("stock", "meme", "commodity", "etf"))
    mid = f"，其中{vol_bits}" if vol_bits else ""
    aum_row = aum.get(day)
    aum_text = ""
    if aum_row:
        aum_bits = split_bits(aum_row, AUM_LABELS, ("stock", "etf", "commodity", "treasury"))
        extra = f"（{aum_bits}）" if aum_bits else ""
        aum_text = f"当日代币化资产市值合计约 {usd_cn(aum_row.get('total', 0.0))}{extra}。"
    elif aum:
        latest_aum = max(aum)
        aum_text = f"市值序列最新为 {latest_aum}，与成交日不是同一天。"
    return (
        f"Robinhood Chain RWA 日度：{day}\n"
        f"据吴说数据中心统计，{cn_day(day)} Robinhood Chain 网络 RWA 相关代币成交合计约 "
        f"{usd_cn(total)}{mid}。{aum_text}{dod}"
        f"口径为 Dune（adam_tehc）链上完整 UTC 日，与看板 RWA 页同一份表。"
        f"\n{DASHBOARD_URL}"
    )


def build_threshold(
    day: str,
    vol: dict[str, dict[str, float]],
    aum: dict[str, dict[str, float]],
    reasons: list[str],
    *,
    historical: bool,
) -> str:
    row = vol.get(day) or {}
    aum_row = aum.get(day) or {}
    body = (
        f"Robinhood Chain RWA 异动：{day}\n"
        f"吴说获悉，据吴说数据中心统计，{cn_day(day)} Robinhood Chain 网络 RWA 相关代币"
        f"成交合计约 {usd_cn(row.get('total', 0.0))}，"
        f"其中股票现货约 {usd_cn(row.get('stock', 0.0))}、"
        f"Meme×股票约 {usd_cn(row.get('meme', 0.0))}。"
        f"触发原因：{'；'.join(reasons)}。"
    )
    if aum_row:
        body += f"当日市值合计约 {usd_cn(aum_row.get('total', 0.0))}。"
    body += "本条为阈值异动提醒，不是例行日报；口径为 Dune（adam_tehc）链上完整 UTC 日。"
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return body


def marks_for(*, kind: str, historical: bool, ath: bool) -> str:
    if kind == "daily":
        return LARK_MARK_DAILY
    if historical or ath:
        return f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    return LARK_MARK_THRESHOLD


def has_ath(reasons: list[str]) -> bool:
    return any("创序列新高" in item for item in reasons)


def threshold_brief(
    day: str,
    vol: dict[str, dict[str, float]],
    aum: dict[str, dict[str, float]],
    *,
    historical: bool,
) -> dict[str, Any] | None:
    reasons = evaluate_day(vol, aum, day)
    if not reasons:
        return None
    return {
        "id": f"robinhood-rwa-threshold-{day}",
        "kind": "threshold",
        "historical": historical,
        "ath": has_ath(reasons),
        "text": build_threshold(day, vol, aum, reasons, historical=historical),
    }


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
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
        if str(result.get("code", "0")) != "0":
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


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def latest_day(*tables: dict[str, dict[str, float]]) -> str | None:
    days = [day for table in tables for day in table]
    return max(days) if days else None


def _id_day(msg_id: str) -> str:
    return msg_id[-10:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=HISTORY_LIMIT)
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    try:
        directory = resolve_json_dir()
        vol = pivot(load_rows(directory, "rwa_volume"), "series", "volume_usd", classify_volume)
        aum = pivot(load_rows(directory, "rwa_aum"), "asset_class", "aum_usd", classify_aum)
        if not vol and not aum:
            raise RuntimeError(f"{directory} 里 rwa_volume / rwa_aum 都是空的")
    except (OSError, json.JSONDecodeError, FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = latest_day(vol, aum)
    fresh = bool(day and is_fresh(day))
    print(
        f"[robinhood-rwa] json={directory} latest={day} fresh={fresh} "
        f"vol_days={len(vol)} aum_days={len(aum)}"
    )

    briefs: list[dict[str, Any]] = []
    if day and fresh and not args.no_daily and day in vol:
        briefs.append(
            {
                "id": f"robinhood-rwa-daily-{day}",
                "kind": "daily",
                "historical": False,
                "ath": False,
                "text": build_daily(day, vol, aum),
            }
        )
    elif day and not fresh:
        print(f"[info] 最新日 {day} 超过 {FRESH_DAYS} 天，跳过实时日常")

    if day and fresh:
        current = threshold_brief(day, vol, aum, historical=False)
        if current:
            briefs.append(current)
        else:
            print(f"[info] 最新日未创新高、分位也未到 {PCTL_LINE:.0f}")

    if args.history_scan and vol:
        cutoff = (
            datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=DEFAULT_HISTORY_DAYS)
        ).isoformat() if day else "1970-01-01"
        candidates: list[dict[str, Any]] = []
        for end in sorted(set(vol) | set(aum)):
            if end == day or end < cutoff:
                continue
            brief = threshold_brief(end, vol, aum, historical=True)
            if brief:
                candidates.append(brief)
        # 新高优先，同一档里取最近日期，避免高位横盘占满回扫名额。
        candidates.sort(key=lambda item: (item["ath"], _id_day(item["id"])), reverse=True)
        briefs.extend(candidates[: max(0, int(args.history_limit))])

    state = load_state()
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    unique.sort(key=lambda item: (0 if item["kind"] == "threshold" else 1, item["id"]))

    for brief in unique:
        mark = marks_for(kind=brief["kind"], historical=brief["historical"], ath=brief["ath"])
        print("---")
        print(
            f"id={brief['id']} historical={brief['historical']} ath={brief['ath']} "
            f"already={brief['id'] in state['sentIds']}"
        )
        print(f"{mark}\n{brief['text']}")
    if not unique:
        print("[skip] 没有可发内容")
        return 0
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in unique:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        mark = marks_for(kind=brief["kind"], historical=brief["historical"], ath=brief["ath"])
        text = f"{mark}\n{brief['text']}"
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text, extra=f"err={exc}")
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
