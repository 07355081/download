#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Polymarket 官方状态页（Instatus）→ Lark 新闻。

免费：https://status.polymarket.com/summary.json
只发最新故障/维护的状态变化与恢复；首跑建基线不补历史；全部正常不发日常。

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
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "polymarket_status.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
SUMMARY_URL = "https://status.polymarket.com/summary.json"
COMPONENTS_URL = "https://status.polymarket.com/v2/components.json"
RSS_URL = "https://status.polymarket.com/history.rss"
PAGE_URL = "https://status.polymarket.com/"
DASHBOARD = "https://data.wublock123.com/dashboard/lark-feeds/polymarket-status"
UA = "Mozilla/5.0 (compatible; dashboard-data/polymarket-status)"
TZ_CN = timezone(timedelta(hours=8))
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
STATUS_ZH = {
    "UP": "正常",
    "HASISSUES": "存在故障",
    "UNDERMAINTENANCE": "维护中",
    "OPERATIONAL": "正常",
    "PARTIALOUTAGE": "部分中断",
    "MAJOROUTAGE": "重大中断",
    "DEGRADEDPERFORMANCE": "性能下降",
    "UNDERMAINTENANCE": "维护中",
    "NOTSTARTEDYET": "已排期未开始",
    "INPROGRESS": "进行中",
    "COMPLETED": "已完成",
    "INVESTIGATING": "正在排查",
    "IDENTIFIED": "已定位原因",
    "MONITORING": "正在观察",
    "RESOLVED": "已恢复",
    "PARTIAL_OUTAGE": "部分中断",
    "MAJOR_OUTAGE": "重大中断",
    "DEGRADED_PERFORMANCE": "性能下降",
    "UNDER_MAINTENANCE": "维护中",
}
# 交易相关组件优先
CORE_SUBSTR = ("CLOB", "Trading", "Predictions", "API", "Perpetual")

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
        raise RuntimeError("状态页返回不是对象")
    return payload


def http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


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
    state["seenIds"] = state["seenIds"][-500:]
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def write_json(payload: dict[str, Any]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def send_lark(webhook: str, secret: str | None, text: str) -> None:
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
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False) + "\n"
        )


def status_zh(value: str) -> str:
    key = str(value or "").strip()
    return STATUS_ZH.get(key, STATUS_ZH.get(key.upper().replace(" ", ""), key or "未知"))


def stamp_cn() -> str:
    now = datetime.now(TZ_CN)
    return f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"


def parse_time(value: str) -> datetime | None:
    text = str(value or "").replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def fmt_cn_time(value: str) -> str:
    dt = parse_time(value)
    if dt is None:
        return ""
    local = dt.astimezone(TZ_CN)
    return f"{local.year} 年 {local.month} 月 {local.day} 日 {local.hour:02d}:{local.minute:02d}"


def clip(text: str, n: int = 200) -> str:
    body = " ".join(str(text or "").split())
    if len(body) > n:
        return body[:n].rstrip() + "…"
    return body


def normalize_events(summary: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key, kind in (("activeIncidents", "incident"), ("activeMaintenances", "maintenance")):
        for row in summary.get(key) or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            last = row.get("lastUpdate") if isinstance(row.get("lastUpdate"), dict) else {}
            events.append(
                {
                    "id": str(row["id"]),
                    "kind": kind,
                    "name": str(row.get("name") or ("故障" if kind == "incident" else "维护")),
                    "status": str(row.get("status") or ""),
                    "url": str(row.get("url") or PAGE_URL),
                    "start": str(row.get("start") or row.get("started") or ""),
                    "message": str(last.get("message") or ""),
                    "updatedAt": str(row.get("updatedAt") or last.get("createdAt") or ""),
                }
            )
    return events


def component_rows(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in components:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        status = str(row.get("status") or "")
        if not name or not status:
            continue
        group = row.get("group")
        group_name = str(group.get("name") or "") if isinstance(group, dict) else ""
        out.append({"name": name, "status": status, "group": group_name})
    return out


def core_down(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in components:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        status = str(row.get("status") or "")
        if not name or not status:
            continue
        if status.upper() in {"OPERATIONAL", "UP"}:
            continue
        if any(s.lower() in name.lower() for s in CORE_SUBSTR):
            out.append({"name": name, "status": status})
    return out


def news_event(ev: dict[str, Any], resolved: bool = False) -> str:
    stamp = stamp_cn()
    title = ev.get("name") or "Polymarket 服务异常"
    link = ev.get("url") or PAGE_URL
    if resolved:
        official = f"官方页面：{link}。" if link and link.rstrip("/") != PAGE_URL.rstrip("/") else ""
        return (
            f"据吴说数据中心统计，截至 {stamp}（UTC+8），Polymarket 官方状态页显示，"
            f"「{title}」已恢复/结束。{official}"
            f"\n详见：{DASHBOARD}"
        )
    phase = status_zh(str(ev.get("status") or ""))
    start = fmt_cn_time(str(ev.get("start") or ""))
    when = f"计划开始时间 {start}（UTC+8）。" if start else ""
    msg = clip(str(ev.get("message") or ""))
    official = f"官方说明：{msg}" if msg else ""
    kind = "维护" if ev.get("kind") == "maintenance" else "故障"
    source = f"官方页面：{link}。" if link and link.rstrip("/") != PAGE_URL.rstrip("/") else ""
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），Polymarket 官方状态页显示，"
        f"{kind}「{title}」，当前状态为{phase}。{when}{official}{source}"
        f"\n详见：{DASHBOARD}"
    )


def news_components(down: list[dict[str, str]]) -> str:
    stamp = stamp_cn()
    bits = "、".join(f"{row['name']}（{status_zh(row['status'])}）" for row in down)
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），Polymarket 官方状态页显示，{bits}。"
        f"\n详见：{DASHBOARD}"
    )


def seed_from_rss(state: dict[str, Any]) -> int:
    """记下 RSS 里已结束条目的 guid，避免误补历史。"""
    try:
        raw = http_bytes(RSS_URL)
    except Exception:
        return 0
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return 0
    n = 0
    for item in root.findall(".//item"):
        guid = (item.findtext("guid") or item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").lower()
        if not guid:
            continue
        # 用 guid 末段作 id
        eid = guid.rstrip("/").split("/")[-1]
        if not eid:
            continue
        if "resolved" in title or "completed" in title or "recover" in title:
            mid = f"pm-{eid}-seed"
            if mid not in state["seenIds"]:
                state["seenIds"].append(mid)
            # also mark generic resolved ids
            for suffix in ("RESOLVED", "COMPLETED", "resolved", "completed"):
                mid2 = f"pm-{eid}-{suffix}"
                if mid2 not in state["seenIds"]:
                    state["seenIds"].append(mid2)
            n += 1
    return n


def push_one(state: dict[str, Any], msg_id: str, text: str, do_push: bool) -> None:
    print("---")
    print(f"id={msg_id} already={msg_id in state['seenIds']}")
    print(text)
    if msg_id in state["seenIds"]:
        print("[skip] 已发过")
        return
    if not do_push:
        print("[dry-run] 未推送")
        return
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        raise RuntimeError("未配置 LARK_ALERT_WEBHOOK")
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except Exception as exc:
        append_log(stamp, msg_id, False, text)
        raise RuntimeError(f"Lark 失败 {msg_id}: {exc}") from exc
    print(f"[pushed] {msg_id}")
    state["seenIds"].append(msg_id)
    append_log(stamp, msg_id, True, text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    try:
        summary = http_json(SUMMARY_URL)
        components_doc = http_json(COMPONENTS_URL)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    page = summary.get("page") if isinstance(summary.get("page"), dict) else {}
    page_status = str(page.get("status") or "")
    events = normalize_events(summary)
    components = [r for r in (components_doc.get("components") or []) if isinstance(r, dict)]
    down = core_down(components)

    write_json(
        {
            "name": "polymarket_status",
            "updated_at": now_iso(),
            "source": SUMMARY_URL,
            "page": DASHBOARD,
            "page_status": page_status,
            "events": events,
            "components": component_rows(components),
            "components_down": down,
            "note": "只发最新故障/维护状态变化与恢复；首跑建基线不补历史；正常不发日常。",
        }
    )
    print(
        f"[polymarket-status] page={page_status or 'none'} events={len(events)} core_down={len(down)}"
    )

    state = load_state()
    if not state["seeded"]:
        n_rss = seed_from_rss(state)
        for ev in events:
            # 当前已存在的排期/故障：建基线，不发
            for suffix in (ev["status"], "seed"):
                mid = f"pm-{ev['id']}-{suffix}"
                if mid not in state["seenIds"]:
                    state["seenIds"].append(mid)
            state["active"][ev["id"]] = {
                "status": ev["status"],
                "name": ev["name"],
                "kind": ev["kind"],
                "url": ev["url"],
            }
        state["seeded"] = True
        save_state(state)
        print(f"[seed] RSS约 {n_rss} 条 + 当前事件 {len(events)} 条已建基线，不补发")
        return 0

    active_now: dict[str, dict[str, Any]] = {}
    briefs: list[tuple[str, str]] = []
    for ev in events:
        eid = ev["id"]
        status = ev["status"]
        active_now[eid] = {
            "status": status,
            "name": ev["name"],
            "kind": ev["kind"],
            "url": ev["url"],
        }
        previous = state["active"].get(eid)
        prev_status = previous.get("status") if isinstance(previous, dict) else previous
        if prev_status == status:
            continue
        msg_id = f"pm-{eid}-{status}"
        briefs.append((msg_id, news_event(ev, resolved=False)))

    for eid, previous in list(state["active"].items()):
        if eid in active_now:
            continue
        prev = previous if isinstance(previous, dict) else {"name": "此前事项", "url": PAGE_URL}
        msg_id = f"pm-{eid}-RESOLVED"
        briefs.append(
            (
                msg_id,
                news_event(
                    {
                        "id": eid,
                        "name": prev.get("name") or "此前事项",
                        "url": prev.get("url") or PAGE_URL,
                        "status": "RESOLVED",
                        "kind": prev.get("kind") or "incident",
                    },
                    resolved=True,
                ),
            )
        )

    if not active_now and down:
        signature = ",".join(f"{row['name']}:{row['status']}" for row in down)
        msg_id = "pm-core-" + hashlib.sha1(signature.encode()).hexdigest()[:10]
        if msg_id not in state["seenIds"]:
            briefs.append((msg_id, news_components(down)))

    # 整站非 UP 且无事件时兜底
    if page_status and page_status.upper() not in {"UP", "OPERATIONAL"} and not events and not down:
        msg_id = f"pm-page-{page_status}"
        if msg_id not in state["seenIds"]:
            briefs.append(
                (
                    msg_id,
                    f"据吴说数据中心统计，截至 {stamp_cn()}（UTC+8），Polymarket 官方状态页显示整站状态为"
                    f"{status_zh(page_status)}。\n详见：{DASHBOARD}",
                )
            )

    state["active"] = active_now
    if not briefs:
        print("[skip] 没有新的故障/维护变化")
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
