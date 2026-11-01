#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pyth 官方状态页 → Lark 新闻。

数据用 status.pyth.network 公开接口，不占付费额度。
只发当前故障的最新状态，以及恢复那一条。已经结束的旧故障不补发。
全部正常时不发日常流水。

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
OUT_PATH = HERE / "output" / "json" / "pyth_status.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
LOCAL_MIRROR = ROOT.parent / "data-dashboard" / "public" / "json" / "pyth-status" / "pyth_status.json"
SUMMARY_URL = "https://status.pyth.network/api/v2/summary.json"
INCIDENTS_URL = "https://status.pyth.network/api/v2/incidents.json"
PAGE_URL = "https://status.pyth.network/"
UA = "Mozilla/5.0"
TZ_CN = timezone(timedelta(hours=8))
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
}
CORE_NAMES = {"Pythnet", "Hermes", "Live Data", "Historical Data", "Benchmarks", "Fortuna"}

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


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Pyth 状态页返回不是对象")
    return payload


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"seeded": False, "seenIds": [], "active": {}}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seeded": False, "seenIds": [], "active": {}}
    seen = doc.get("seenIds") if isinstance(doc, dict) else None
    active = doc.get("active") if isinstance(doc, dict) else None
    return {
        "seeded": bool(doc.get("seeded")) if isinstance(doc, dict) else False,
        "seenIds": [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else [],
        "active": active if isinstance(active, dict) else {},
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


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
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


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


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


def duration_phrase(incident: dict[str, Any]) -> str:
    started = parse_time(str(incident.get("started_at") or incident.get("created_at") or ""))
    if started is None:
        return ""
    hours = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() / 3600
    if hours < 1:
        return "已持续不到 1 小时。"
    return f"已持续约 {hours:.0f} 小时。"


def latest_update(incident: dict[str, Any]) -> str:
    updates = incident.get("incident_updates") or []
    if not isinstance(updates, list) or not updates or not isinstance(updates[0], dict):
        return ""
    body = " ".join(str(updates[0].get("body") or "").split())
    if len(body) > 180:
        body = body[:180].rstrip() + "…"
    return body


def component_names(incident: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in incident.get("components") or []:
        if isinstance(row, dict) and row.get("name"):
            names.append(str(row["name"]))
    return names


def news_for_incident(incident: dict[str, Any], resolved: bool) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    title = str(incident.get("name") or "Pyth 服务异常")
    names = "、".join(component_names(incident))
    update = latest_update(incident)
    if resolved:
        extra = f"受影响组件包括 {names}。" if names else ""
        return (
            f"据吴说数据中心统计，截至 {stamp}（UTC+8），Pyth 官方状态页显示，{title}已恢复。{extra}"
            f"\n{PAGE_URL}"
        )
    phase = status_zh(str(incident.get("status") or ""))
    comp = f"受影响组件包括 {names}。" if names else ""
    official = f"官方最新说明：{update}" if update else ""
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），Pyth 官方状态页显示，{title}，当前{phase}。{comp}"
        f"{duration_phrase(incident)}{official}"
        f"\n{PAGE_URL}"
    )


def news_for_components(down: list[dict[str, str]]) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    bits = "、".join(f"{row['name']}{status_zh(row['status'])}" for row in down)
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），Pyth 官方状态页显示，{bits}。"
        f"\n{PAGE_URL}"
    )


def core_down(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in components:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        status = str(row.get("status") or "")
        if name in CORE_NAMES and status and status != "operational":
            out.append({"name": name, "status": status})
    return out


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
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    page_status = summary.get("status") if isinstance(summary.get("status"), dict) else {}
    components = [row for row in (summary.get("components") or []) if isinstance(row, dict)]
    incidents = [row for row in (summary.get("incidents") or []) if isinstance(row, dict) and row.get("id")]
    indicator = str(page_status.get("indicator") or "")
    description = str(page_status.get("description") or "")
    down = core_down(components)
    write_json({
        "updated_at": now_iso(),
        "source": "https://status.pyth.network/api/v2/summary.json",
        "note": "只在出现新的未恢复故障、状态变化或恢复时发新闻。已结束的旧故障不补发。全部正常不发日常。",
        "indicator": indicator,
        "description": description,
        "components": [{"name": row.get("name"), "status": row.get("status")} for row in components],
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
    })
    print(f"[pyth-status] indicator={indicator or 'none'} incidents={len(incidents)} core_down={len(down)}")

    state = load_state()
    if not state["seeded"]:
        try:
            history = http_json(INCIDENTS_URL)
        except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] 读历史故障失败: {exc}", file=sys.stderr)
            return 1
        for row in history.get("incidents") or []:
            if isinstance(row, dict) and row.get("id") and str(row.get("status")) == "resolved":
                state["seenIds"].append(str(row["id"]))
        state["seeded"] = True
        state["active"] = {}
        print(f"[seed] 已记下 {len(state['seenIds'])} 条已恢复故障，不补发")

    active_now: dict[str, dict[str, Any]] = {}
    briefs: list[tuple[str, str]] = []
    for incident in incidents:
        incident_id = str(incident["id"])
        status = str(incident.get("status") or "")
        if status == "resolved":
            continue
        active_now[incident_id] = {
            "status": status,
            "name": str(incident.get("name") or "Pyth 服务异常"),
            "components": component_names(incident),
        }
        previous = state["active"].get(incident_id)
        previous_status = previous.get("status") if isinstance(previous, dict) else previous
        if previous_status == status:
            continue
        msg_id = f"pyth-{incident_id}-{status}"
        briefs.append((msg_id, news_for_incident(incident, resolved=False)))
    for incident_id, previous in state["active"].items():
        if incident_id in active_now:
            continue
        name = previous.get("name") if isinstance(previous, dict) else "此前的故障"
        names = previous.get("components") if isinstance(previous, dict) else []
        msg_id = f"pyth-{incident_id}-resolved"
        briefs.append((
            msg_id,
            news_for_incident(
                {"id": incident_id, "name": name, "status": "resolved", "components": [{"name": item} for item in names or []]},
                resolved=True,
            ),
        ))
    if not active_now and down:
        signature = ",".join(f"{row['name']}:{row['status']}" for row in down)
        msg_id = "pyth-core-" + hashlib.sha1(signature.encode()).hexdigest()[:10]
        if msg_id not in state["seenIds"]:
            briefs.append((msg_id, news_for_components(down)))

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
