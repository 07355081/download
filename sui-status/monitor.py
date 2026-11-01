#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sui 主网状态页 + 公开 GraphQL 最新检查点 → Lark 新闻。

免费：status.sui.io、graphql.mainnet.sui.io。不占付费额度。
只发主网新故障、状态变化、检查点停住，以及恢复那一条。
已结束的旧故障不补发。全部正常时不发日常。

用法：
  python monitor.py
  python monitor.py --push
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
OUT_PATH = HERE / "output" / "json" / "sui_status.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
LOCAL_MIRROR = ROOT.parent / "data-dashboard" / "public" / "json" / "sui-status" / "sui_status.json"
SUMMARY_URL = "https://status.sui.io/api/v2/summary.json"
INCIDENTS_URL = "https://status.sui.io/api/v2/incidents.json"
GQL_URL = "https://graphql.mainnet.sui.io/graphql"
GQL_QUERY = "{ checkpoint { sequenceNumber timestamp } }"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/sui-status"
UA = "Mozilla/5.0 (compatible; dashboard-data/sui-status)"
TZ_CN = timezone(timedelta(hours=8))
STALL_SEC = 600
CLEAR_SEC = 120
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
STATUS_ZH = {
    "operational": "正常",
    "degraded_performance": "性能下降",
    "partial_outage": "部分中断",
    "major_outage": "重大中断",
    "under_maintenance": "维护中",
    "investigating": "正在排查",
    "identified": "已定位原因",
    "monitoring": "正在观察",
    "resolved": "已恢复",
    "postmortem": "已恢复",
    "none": "全部正常",
}
MAINNET_NAMES = {"Sui Mainnet - Validators", "Sui Mainnet - Public RPC Nodes"}
CLOSED = {"resolved", "postmortem"}

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def http_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("返回不是对象")
    return body


def load_state() -> dict[str, Any]:
    empty = {"seeded": False, "seenIds": [], "active": {}, "stallOpen": False}
    if not STATE_PATH.is_file():
        return empty
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(doc, dict):
        return empty
    seen = doc.get("seenIds")
    active = doc.get("active")
    return {
        "seeded": bool(doc.get("seeded")),
        "seenIds": [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else [],
        "active": active if isinstance(active, dict) else {},
        "stallOpen": bool(doc.get("stallOpen")),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["seenIds"] = state["seenIds"][-400:]
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def write_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    paths = [OUT_PATH]
    if LOCAL_MIRROR.parents[2].is_dir():
        paths.append(LOCAL_MIRROR)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False) + "\n")


def status_zh(value: str) -> str:
    return STATUS_ZH.get(value, value or "未知")


def parse_time(value: str) -> datetime | None:
    text = str(value or "").replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def stamp_cn() -> str:
    now = datetime.now(TZ_CN)
    return f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"


def fmt_cn(value: str) -> str:
    moment = parse_time(value)
    if moment is None:
        return value
    local = moment.astimezone(TZ_CN)
    return f"{local.month} 月 {local.day} 日 {local.hour:02d}:{local.minute:02d}（UTC+8）"


def component_names(incident: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in incident.get("components") or []:
        if isinstance(row, dict) and row.get("name"):
            names.append(str(row["name"]))
    return names


def mainnet_incident(incident: dict[str, Any]) -> bool:
    names = component_names(incident)
    if not names:
        return True
    return any("mainnet" in name.lower() for name in names)


def latest_update(incident: dict[str, Any]) -> str:
    updates = incident.get("incident_updates") or []
    if not isinstance(updates, list) or not updates or not isinstance(updates[0], dict):
        return ""
    body = " ".join(str(updates[0].get("body") or "").split())
    if len(body) > 180:
        body = body[:180].rstrip() + "…"
    return body


def incident_page_rows(rows: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recent: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not mainnet_incident(row):
            continue
        started = str(row.get("started_at") or "")
        month = started[:7]
        if len(month) == 7 and month[4] == "-":
            counts[month] = counts.get(month, 0) + 1
        recent.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "status": row.get("status"),
                "impact": row.get("impact"),
                "started_at": row.get("started_at"),
            }
        )
    recent.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    months: list[dict[str, Any]] = []
    if counts:
        keys = sorted(counts)
        year, mon = (int(part) for part in keys[0].split("-"))
        end_year, end_mon = (int(part) for part in keys[-1].split("-"))
        while (year, mon) <= (end_year, end_mon):
            key = f"{year:04d}-{mon:02d}"
            months.append({"month": key, "count": counts.get(key, 0)})
            mon += 1
            if mon == 13:
                mon = 1
                year += 1
    return recent[:8], months


def core_down(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in components:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        status = str(row.get("status") or "")
        if name in MAINNET_NAMES and status and status != "operational":
            out.append({"name": name, "status": status})
    return out


def fetch_checkpoint() -> dict[str, Any]:
    body = http_json(GQL_URL, {"query": GQL_QUERY})
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    row = (body.get("data") or {}).get("checkpoint") if isinstance(body.get("data"), dict) else None
    if not isinstance(row, dict) or not row.get("timestamp"):
        raise RuntimeError("checkpoint 为空")
    moment = parse_time(str(row["timestamp"]))
    if moment is None:
        raise RuntimeError("checkpoint 时间无法解析")
    age = max(0, int((datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds()))
    sequence = row.get("sequenceNumber")
    return {
        "sequence": sequence,
        "time": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_sec": age,
        "stalled": age >= STALL_SEC,
    }


def news_incident(incident: dict[str, Any], resolved: bool) -> str:
    title = str(incident.get("name") or "Sui 主网异常")
    names = "、".join(name for name in component_names(incident) if "mainnet" in name.lower())
    extra = f"受影响组件包括 {names}。" if names else ""
    if resolved:
        return (
            f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Sui 官方状态页显示，{title}已恢复。{extra}"
            f"详见：{PAGE}"
        )
    phase = status_zh(str(incident.get("status") or ""))
    update = latest_update(incident)
    official = f"官方最新说明：{update}" if update else ""
    return (
        f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Sui 官方状态页显示，{title}，当前{phase}。{extra}{official}"
        f"详见：{PAGE}"
    )


def news_components(down: list[dict[str, str]]) -> str:
    bits = "、".join(f"{row['name']}{status_zh(row['status'])}" for row in down)
    return f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Sui 官方状态页显示，{bits}。详见：{PAGE}"


def news_stall(checkpoint: dict[str, Any]) -> str:
    minutes = max(1, int(checkpoint["age_sec"]) // 60)
    return (
        f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Sui 主网公开节点最新检查点停留在 {fmt_cn(str(checkpoint['time']))}，"
        f"已约 {minutes} 分钟没有新检查点。"
        f"详见：{PAGE}"
    )


def news_stall_clear(checkpoint: dict[str, Any]) -> str:
    return (
        f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Sui 主网公开检查点已恢复推进，"
        f"最新检查点时间 {fmt_cn(str(checkpoint['time']))}。"
        f"详见：{PAGE}"
    )


def push_one(state: dict[str, Any], msg_id: str, text: str, do_push: bool) -> None:
    print("---")
    print(f"id={msg_id} already={msg_id in state['seenIds']}")
    print(text)
    if msg_id in state["seenIds"]:
        print("[skip] 这条已经发过")
        return
    if not do_push:
        print("[dry-run] 未推送")
        state["seenIds"].append(msg_id)
        return
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        raise RuntimeError("未配置 LARK_ALERT_WEBHOOK")
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        append_log(stamp, msg_id, False, text)
        raise RuntimeError(f"Lark 失败 {msg_id}: {exc}") from exc
    print(f"[pushed] {msg_id}")
    state["seenIds"].append(msg_id)
    append_log(stamp, msg_id, True, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    try:
        summary = http_json(SUMMARY_URL)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 状态页 {exc}", file=sys.stderr)
        return 1

    page_status = summary.get("status") if isinstance(summary.get("status"), dict) else {}
    components = [row for row in (summary.get("components") or []) if isinstance(row, dict)]
    incidents = [
        row
        for row in (summary.get("incidents") or [])
        if isinstance(row, dict) and row.get("id") and mainnet_incident(row)
    ]
    indicator = str(page_status.get("indicator") or "")
    description = str(page_status.get("description") or "")
    down = core_down(components)
    recent_incidents: list[dict[str, Any]] = []
    incident_months: list[dict[str, Any]] = []
    try:
        history = http_json(INCIDENTS_URL)
        recent_incidents, incident_months = incident_page_rows(history.get("incidents") or [])
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[WARN] 故障历史 {exc}", file=sys.stderr)
    checkpoint: dict[str, Any] | None = None
    checkpoint_error = ""
    try:
        checkpoint = fetch_checkpoint()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        checkpoint_error = str(exc)
        print(f"[WARN] 检查点 {exc}", file=sys.stderr)

    write_json(
        {
            "updated_at": now_iso(),
            "source": "Sui 官方状态页 + 主网公开 GraphQL 最新检查点",
            "page": PAGE,
            "note": "只在主网出现新故障、状态变化、检查点超过 10 分钟未推进，或恢复时发新闻。已结束的旧故障不补发。全部正常不发日常。",
            "indicator": indicator,
            "description": description,
            "components": [
                {"name": row.get("name"), "status": row.get("status")}
                for row in components
                if str(row.get("name") or "") in MAINNET_NAMES
            ],
            "incidents": [
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "impact": row.get("impact"),
                    "started_at": row.get("started_at"),
                }
                for row in incidents
            ],
            "recent_incidents": recent_incidents,
            "incident_months": incident_months,
            "checkpoint": checkpoint,
            "checkpoint_error": checkpoint_error,
        }
    )
    age = checkpoint.get("age_sec") if checkpoint else None
    print(
        f"[sui-status] indicator={indicator or 'none'} incidents={len(incidents)} "
        f"mainnet_down={len(down)} checkpoint_age={age}"
    )

    state = load_state()
    if not state["seeded"]:
        try:
            history = http_json(INCIDENTS_URL)
        except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] 读历史故障失败: {exc}", file=sys.stderr)
            return 1
        for row in history.get("incidents") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            if str(row.get("status") or "") not in CLOSED:
                continue
            incident_id = str(row["id"])
            for msg_id in (incident_id, f"sui-{incident_id}-resolved", f"sui-{incident_id}-{row.get('status')}"):
                if msg_id not in state["seenIds"]:
                    state["seenIds"].append(msg_id)
        state["seeded"] = True
        state["active"] = {}
        state["stallOpen"] = bool(checkpoint and checkpoint["stalled"])
        print(f"[seed] 已记下 {len(state['seenIds'])} 条已恢复故障，不补发")
        if state["stallOpen"]:
            print("[seed] 当前检查点已停住，首跑只记基线，不补发这一次")
        save_state(state)
        return 0

    briefs: list[tuple[str, str]] = []
    active_now: dict[str, dict[str, Any]] = {}
    for incident in incidents:
        incident_id = str(incident["id"])
        status = str(incident.get("status") or "")
        if status in CLOSED:
            continue
        active_now[incident_id] = {
            "status": status,
            "name": str(incident.get("name") or "Sui 主网异常"),
            "components": component_names(incident),
        }
        previous = state["active"].get(incident_id)
        previous_status = previous.get("status") if isinstance(previous, dict) else previous
        if previous_status == status:
            continue
        briefs.append((f"sui-{incident_id}-{status}", news_incident(incident, resolved=False)))
    for incident_id, previous in state["active"].items():
        if incident_id in active_now:
            continue
        name = previous.get("name") if isinstance(previous, dict) else "此前的故障"
        names = previous.get("components") if isinstance(previous, dict) else []
        briefs.append(
            (
                f"sui-{incident_id}-resolved",
                news_incident(
                    {
                        "id": incident_id,
                        "name": name,
                        "status": "resolved",
                        "components": [{"name": item} for item in names or []],
                    },
                    resolved=True,
                ),
            )
        )
    if not active_now and down:
        signature = ",".join(f"{row['name']}:{row['status']}" for row in down)
        msg_id = "sui-core-" + hashlib.sha1(signature.encode()).hexdigest()[:10]
        if msg_id not in state["seenIds"]:
            briefs.append((msg_id, news_components(down)))
    if checkpoint and checkpoint["stalled"] and not state["stallOpen"]:
        briefs.append((f"sui-stall-{checkpoint.get('sequence')}", news_stall(checkpoint)))
        state["stallOpen"] = True
    elif checkpoint and int(checkpoint["age_sec"]) <= CLEAR_SEC and state["stallOpen"]:
        briefs.append((f"sui-stall-clear-{checkpoint.get('sequence')}", news_stall_clear(checkpoint)))
        state["stallOpen"] = False

    state["active"] = active_now
    if not briefs:
        print("[skip] 没有新的故障或恢复")
        save_state(state)
        return 0
    try:
        for msg_id, text in briefs:
            push_one(state, msg_id, text, args.push)
            if args.push:
                time.sleep(0.4)
    except RuntimeError as exc:
        save_state(state)
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
