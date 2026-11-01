# -*- coding: utf-8 -*-
"""XRP 期货未平仓 → Lark。

- 每个已结束的 UTC 日：【实时日常报告】价格、全市场未平仓、CME 未平仓和份额，并带上近 14 日变化
- CME 份额比此前最高再高至少 2 个百分点，或 CME 未平仓比此前最高再高至少 5%：
  【历史阈值警报】再加【特殊警报】
- 不回扫旧日期。9 月 1 日那篇不补推。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "xrp_oi.json",
    Path("/root/dashboard/public/json/xrp-oi/xrp_oi.json"),
]
STATE_PATH = HERE / "output" / "xrp_oi_lark.json"
LOG_PATH = HERE / "xrp_oi_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/xrp-oi"
MIN_PRIOR = 30
SHARE_PP = 2.0
OI_BREAK = 1.05


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


def load_doc(explicit: str) -> tuple[Path, dict[str, Any]]:
    paths = [Path(explicit)] if explicit else JSON_CANDIDATES
    for path in paths:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("history"), list):
                return path, doc
    raise FileNotFoundError("找不到 xrp_oi.json")


def cn_day(day: str) -> str:
    year, month, date = day.split("-")
    return f"{year} 年 {int(month)} 月 {int(date)} 日"


def yi(n: int) -> str:
    return f"{abs(n) / 1e8:.2f} 亿枚"


def pct_text(curr: float, prev: float) -> str:
    pct = (curr / prev - 1.0) * 100.0
    verb = "增长" if pct >= 0 else "下降"
    return f"{verb} {abs(pct):.1f}%"


def coin_text(curr: int, prev: int) -> str:
    delta = curr - prev
    verb = "增加" if delta >= 0 else "减少"
    return f"{verb} {yi(delta)}"


def prior_row(rows: list[dict[str, Any]], day: str, back: int) -> dict[str, Any] | None:
    want = (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=back)).isoformat()
    for row in rows:
        if str(row.get("time")) == want:
            return row
    return None


def core(row: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    day = str(row["time"])
    text = (
        f"据吴说数据中心统计，截至 {cn_day(day)}，XRP 价格约 {float(row['price']):.2f} 美元。"
        f"全市场 XRP 期货未平仓量约 {yi(int(row['global_oi']))}，"
        f"CME 约 {yi(int(row['cme_oi']))}，市场份额约 {float(row['share_pct']):.1f}%。"
    )
    old = prior_row(rows, day, 14)
    if old is not None and float(old["price"]) > 0 and int(old["global_oi"]) > 0 and int(old["cme_oi"]) > 0:
        text += (
            f"近 14 日价格{pct_text(float(row['price']), float(old['price']))}，"
            f"全市场未平仓量{pct_text(int(row['global_oi']), int(old['global_oi']))}，"
            f"CME 未平仓量{pct_text(int(row['cme_oi']), int(old['cme_oi']))}，"
            f"市场份额由 {float(old['share_pct']):.1f}% 变为 {float(row['share_pct']):.1f}%。"
            f"其他平台未平仓量合计{coin_text(int(row['others_oi']), int(old['others_oi']))}。"
        )
    return text + f"\n{DASHBOARD_URL}"


def is_record(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    prior = [r for r in rows if str(r.get("time")) < str(row["time"])]
    if len(prior) < MIN_PRIOR:
        return False
    share = float(row["share_pct"])
    cme = int(row["cme_oi"])
    max_share = max(float(r["share_pct"]) for r in prior)
    max_cme = max(int(r["cme_oi"]) for r in prior)
    return share >= max_share + SHARE_PP or cme >= int(max_cme * OI_BREAK)


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
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
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return {"sentIds": [str(x) for x in ids] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def closed_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = [r for r in rows if str(r.get("time")) < today]
    if not done:
        raise RuntimeError("没有已结束的日期")
    return done[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    try:
        path, doc = load_doc(args.json)
        rows = [r for r in doc["history"] if isinstance(r, dict) and r.get("time")]
        rows.sort(key=lambda r: str(r["time"]))
        row = closed_row(rows)
    except (OSError, json.JSONDecodeError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    print(f"[xrp-oi] json={path} as_of={day} age={age}d")
    if age > 3:
        print("[ERROR] 最新数据超过 3 天，不发", file=sys.stderr)
        return 1

    briefs = []
    if is_record(row, rows):
        text = "这是历史高点，不是日常新闻。CME 的 XRP 期货未平仓或份额高于此前记录。" + core(row, rows)
        briefs.append(("threshold", f"xrp-oi-threshold-{day}", f"【历史阈值警报】\n【特殊警报】\n{text}"))
    briefs.append(("daily", f"xrp-oi-daily-{day}", f"【实时日常报告】\n{core(row, rows)}"))

    state = load_state()
    for kind, msg_id, text in briefs:
        print("---")
        print(f"id={msg_id} kind={kind} already={msg_id in state['sentIds']}")
        print(text)
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
    for _kind, msg_id, text in briefs:
        if msg_id in state["sentIds"] and not args.force:
            print(f"skip {msg_id}")
            continue
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {msg_id}")
        state["sentIds"].append(msg_id)
        sent_any = True
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} id={msg_id}\n{text}\n\n")
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-80:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
