# -*- coding: utf-8 -*-
"""Robinhood Chain 活跃度 → Lark。

- 日常：最近一个完整 UTC 日【实时日常报告】（交易笔数、全链 DEX、Pons 创建数与成交）
- 阈值：近 7 日均值较再往前 7 日下跌 ≥20% →【历史阈值警报】
- 新高：最新日仍新鲜，且全链 DEX、日活跃地址或首次交易地址高于此前每一天
  →【历史阈值警报】+【特殊警报】。只报刚闭合的这一天，不回放更早的高峰。
- --history-scan：回扫更早过线的 7 日窗口，抬头加【特殊警报】并写明历史回扫
- 不含 Uniswap v3/v4 拆分。DEX 或 Pons 成交的异常尖峰不参与环比。

用法：
  python activity_flash.py
  python activity_flash.py --push --history-scan
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
STATE_PATH = HERE / "output" / "activity_lark.json"
LOG_PATH = HERE / "activity_flash.log"
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
WINDOW = 7
NOTABLE_PCT = 20.0
MAJOR_PCT = 40.0
FRESH_DAYS = 4
HISTORY_LIMIT = 4
PONS = "ponsfamily.com"
# 9/28 DEX 约 1291 亿美元、9/2 Pons 成交约 345 亿美元，明显不是可用日。
DEX_CAP = 8e10
PONS_VOL_CAP = 2e10


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
        if (path / "dex_volume.json").is_file() or (path / "daily_transactions.json").is_file():
            return path
    raise FileNotFoundError("找不到 robinhood-chain JSON")


def load_rows(directory: Path, name: str) -> list[dict[str, Any]]:
    path = directory / f"{name}.json"
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def day_key(row: dict[str, Any]) -> str | None:
    raw = row.get("day") or row.get("block_date") or row.get("time")
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


def series_from(rows: list[dict[str, Any]], key: str, accumulate: bool = False) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        day = day_key(row)
        if not day:
            continue
        value = num(row, key)
        out[day] = (out.get(day, 0.0) + value) if accumulate else value
    return out


def wow(series: dict[str, float], end_day: str | None = None) -> dict[str, Any] | None:
    """近 7 个连续完整日 vs 再往前 7 个连续日。中间缺日则这条指标不参与。"""
    if not series:
        return None
    end = datetime.strptime(end_day or max(series), "%Y-%m-%d").date()
    days = [(end - timedelta(days=offset)).isoformat() for offset in range(WINDOW * 2 - 1, -1, -1)]
    if any(day not in series for day in days):
        return None
    prior, recent = days[:WINDOW], days[WINDOW:]
    prior_avg = sum(series[day] for day in prior) / WINDOW
    recent_avg = sum(series[day] for day in recent) / WINDOW
    if prior_avg == 0:
        return None
    pct = (recent_avg - prior_avg) / abs(prior_avg) * 100
    return {
        "recent": recent_avg,
        "prior": prior_avg,
        "pct": pct,
        "recent_days": recent,
        "prior_days": prior,
    }


def wan(value: float, unit: str) -> str:
    if abs(value) >= 10000:
        return f"{value / 10000:.1f} 万{unit}"
    return f"{round(value):,} {unit}"


def usd_cn(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f} 万美元"
    return f"{value:.0f} 美元"


def signed_pct(value: float) -> str:
    return f"{value:+.1f}%"


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


def wallet_type_series(rows: list[dict[str, Any]], wallet_type: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        if str(row.get("wallet_type") or "") != wallet_type:
            continue
        day = day_key(row)
        if day:
            out[day] = num(row, "wallets")
    return out


def ath_brief(series: dict[str, float], label: str, id_prefix: str, fmt) -> dict[str, Any] | None:
    """最新日必须新鲜，且高于此前每一天。不回放旧高峰。"""
    end = latest_complete(series)
    if not end:
        return None
    today = series[end]
    prior = [value for day, value in series.items() if day < end]
    if not prior or today <= max(prior):
        return None
    previous = max(prior)
    text = (
        f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}\n"
        f"据吴说数据中心统计，{end} Robinhood Chain {label}约 {fmt(today)}，"
        f"高于此前每一天（此前最高 {fmt(previous)}）。"
        "本条是创历史新高，不是每日流水。"
        f"\n{DASHBOARD_URL}"
    )
    return {
        "id": f"{id_prefix}-{end}",
        "kind": "threshold",
        "historical": False,
        "text": text,
    }


def pons_series(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        if str(row.get("launchpad") or "") != PONS:
            continue
        day = day_key(row)
        if day:
            out[day] = num(row, key)
    return out


def sane(series: dict[str, float], cap: float) -> dict[str, float]:
    return {day: value for day, value in series.items() if value <= cap}


def latest_complete(*series: dict[str, float]) -> str | None:
    days = [day for item in series for day in item]
    if not days:
        return None
    end = max(days)
    age = (datetime.now(timezone.utc).date() - datetime.strptime(end, "%Y-%m-%d").date()).days
    if age > FRESH_DAYS:
        return None
    return end


def build_daily(
    day: str,
    tx: dict[str, float],
    dex: dict[str, float],
    pons_new: dict[str, float],
    pons_vol: dict[str, float],
) -> str:
    bits = [f"据吴说数据中心统计，截至 {day}"]
    if day in tx:
        bits.append(f"Robinhood Chain 日交易约 {wan(tx[day], '笔')}")
    if day in dex:
        bits.append(f"全链 DEX 成交约 {usd_cn(dex[day])}")
    head = "，".join(bits) + "。"
    pons = ""
    if day in pons_new or day in pons_vol:
        created = f"新建代币约 {wan(pons_new[day], '枚')}" if day in pons_new else "新建代币暂无"
        volume = f"成交约 {usd_cn(pons_vol[day])}" if day in pons_vol else "成交暂无"
        pons = f"最大发射器 Pons 当日{created}，{volume}。"
    elif pons_new or pons_vol:
        pons = "Pons 当日尚未刷新（发射器查询不是每天重跑）。"
    return (
        f"Robinhood Chain 日度：{day}\n{head}{pons}"
        "不含 Uniswap v3/v4 拆分；DEX 与 Pons 成交的异常尖峰已从环比里剔除。"
        f"\n{DASHBOARD_URL}"
    )


def build_text(metrics: dict[str, dict[str, Any] | None], worst_key: str, *, historical: bool) -> str:
    worst = metrics[worst_key]
    assert worst is not None
    end = worst["recent_days"][-1]
    start_recent = worst["recent_days"][0]
    start_prior = worst["prior_days"][0]
    tx = metrics.get("txns")
    wallets = metrics.get("wallets")
    dex = metrics.get("dex")

    def piece(label: str, item: dict[str, Any] | None, formatted: str) -> str:
        if item is None:
            return f"{label}暂无"
        return f"{label}约 {formatted}，环比 {signed_pct(item['pct'])}"

    tx_text = "日均交易暂无"
    if tx is not None:
        tx_text = (
            f"日均交易约 {wan(tx['recent'], '笔')}，较前 7 日（自 {start_prior}）环比 {signed_pct(tx['pct'])}"
            if worst_key == "txns"
            else f"日均交易约 {wan(tx['recent'], '笔')}，环比 {signed_pct(tx['pct'])}"
        )
    wallet_text = piece("日均活跃地址", wallets, wan(wallets["recent"], "个") if wallets else "")
    dex_text = piece("日均 DEX 成交", dex, usd_cn(dex["recent"]) if dex else "")
    body = (
        f"据吴说数据中心统计，Robinhood Chain 近 7 日（{start_recent} 至 {end}）{tx_text}；"
        f"{wallet_text}；{dex_text}。"
        f"口径为 Dune 链上完整日，活跃地址为 new 与 returning 合计。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（窗口止于 {end}，发现时间 {found} UTC+8），请勿当成刚发生的一周。"
    return f"{body}\n{DASHBOARD_URL}"


def window_brief(tx_map, wallet_map, dex_map, end: str, *, historical: bool) -> dict[str, Any] | None:
    metrics = {
        "txns": wow(tx_map, end),
        "wallets": wow(wallet_map, end),
        "dex": wow(dex_map, end),
    }
    present = {key: item for key, item in metrics.items() if item is not None}
    breaches = {key: item for key, item in present.items() if item["pct"] <= -NOTABLE_PCT}
    if not breaches:
        return None
    worst_key = min(breaches, key=lambda key: breaches[key]["pct"])
    end_day = breaches[worst_key]["recent_days"][-1]
    mark = LARK_MARK_THRESHOLD
    if historical or abs(breaches[worst_key]["pct"]) >= MAJOR_PCT:
        mark = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    return {
        "id": f"robinhood-wow-slowdown-{end_day}",
        "kind": "threshold",
        "historical": historical,
        "text": f"{mark}\n{build_text(metrics, worst_key, historical=historical)}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    try:
        directory = resolve_json_dir()
        def rows_of(name: str) -> list[dict[str, Any]]:
            for path in JSON_DIR_CANDIDATES:
                found = load_rows(path, name)
                if found:
                    return found
            return []

        tx_map = series_from(rows_of("daily_transactions"), "txns")
        wallet_rows = rows_of("active_wallets")
        wallet_map = series_from(wallet_rows, "wallets", accumulate=True)
        new_wallet_map = wallet_type_series(wallet_rows, "new")
        dex_map = sane(series_from(rows_of("dex_volume"), "dex_volume_usd"), DEX_CAP)
        pons_new = pons_series(rows_of("launchpad_new_tokens"), "tokens_launched")
        pons_vol = sane(pons_series(rows_of("launchpad_activity"), "volume_usd"), PONS_VOL_CAP)
    except (OSError, json.JSONDecodeError, FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = latest_complete(tx_map, dex_map)
    print(f"[robinhood-activity] json={directory} latest={day}")
    briefs: list[dict[str, Any]] = []
    if day and not args.no_daily:
        briefs.append({
            "id": f"robinhood-daily-{day}",
            "kind": "daily",
            "historical": False,
            "text": f"{LARK_MARK_DAILY}\n{build_daily(day, tx_map, dex_map, pons_new, pons_vol)}",
        })
    if day:
        current = window_brief(tx_map, wallet_map, dex_map, day, historical=False)
        if current:
            briefs.append(current)
        else:
            print(f"[info] 最新窗口未跌超 {NOTABLE_PCT:.0f}%")
        for brief in (
            ath_brief(dex_map, "全链 DEX 成交", "robinhood-dex-ath", usd_cn),
            ath_brief(wallet_map, "日活跃地址", "robinhood-wallet-ath", lambda value: wan(value, "个")),
            ath_brief(new_wallet_map, "首次交易地址", "robinhood-new-wallet-ath", lambda value: wan(value, "个")),
        ):
            if brief:
                briefs.append(brief)
    if args.history_scan and tx_map:
        hist_n = 0
        ends = sorted(set(tx_map) | set(dex_map))
        for end in reversed(ends):
            if end == day or hist_n >= HISTORY_LIMIT:
                continue
            nxt = (datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            if nxt != day and window_brief(tx_map, wallet_map, dex_map, nxt, historical=True):
                continue
            brief = window_brief(tx_map, wallet_map, dex_map, end, historical=True)
            if not brief:
                continue
            briefs.append(brief)
            hist_n += 1

    state = load_state()
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    for brief in unique:
        print("---")
        print(f"id={brief['id']} historical={brief['historical']} already={brief['id'] in state['sentIds']}")
        print(brief["text"])
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
        try:
            send_lark_text(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, brief["text"], extra=f"err={exc}")
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, brief["text"])
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
