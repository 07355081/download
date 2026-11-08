# -*- coding: utf-8 -*-
"""BNB Chain RWA 合计 → 新闻（as_news）。

免费 rwa.xyz 公开页 BNB Chain 网络合计，接受与 Blockworks 口径差。
仅：严格高于此前采样峰值且本地对照日≥13，或日涨≥8%，或页内近7日涨≥15%。
冷启动建基线不发；不发日常流水、不加警报角标；不补一年前 6000 万对照窗。
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "bnb_chain_rwa.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "bnb-chain-rwa" / "bnb_chain_rwa.json",
    Path("/root/dashboard/public/json/bnb-chain-rwa/bnb_chain_rwa.json"),
]
STATE_PATH = HERE / "output" / "bnb_chain_rwa_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/bnb-chain-rwa"
DOD_PCT = 8.0
CHG7D_PCT = 15.0
ATH_MIN_DAYS = 14


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 bnb_chain_rwa.json")


def yi(n: float) -> str:
    a = abs(n) / 1e8
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "baselined": False}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "baselined": False}
    if not isinstance(doc, dict):
        return {"sentIds": [], "baselined": False}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baselined": bool(doc.get("baselined")),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(result)


def build_news(latest: dict[str, Any], reason: str) -> str:
    day = str(latest["time"])[:10]
    usd = float(latest["usd"])
    bits = [f"BNB Chain 上 RWA 资产合计市值约 {yi(usd)}"]
    if reason == "ath":
        bits.append("创本通道记录新高")
    dod = latest.get("dod_pct")
    if isinstance(dod, (int, float)) and dod >= DOD_PCT:
        bits.append(f"较上一采样日上涨约 {dod:.1f}%")
    chg7 = latest.get("chg_7d_pct")
    if isinstance(chg7, (int, float)) and chg7 >= CHG7D_PCT:
        bits.append(f"近 7 日上涨约 {chg7:.1f}%")
    share = latest.get("share_pct")
    if isinstance(share, (int, float)) and share > 0:
        bits.append(f"约占 rwa.xyz 全网样本约 {share:.1f}%")
    body = "，".join(bits) + "。"
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {cn_day(day)}，{body}"
        f"口径为 rwa.xyz 公开页按网络汇总的 BNB Chain 全口径 RWA，与 Blockworks 可能不同；"
        f"不含一年前基数对照。"
        f"\n{PAGE}"
    )


def prior_peak(rows: list[Any], day: str) -> tuple[int, float | None]:
    priors: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_day = str(row.get("time") or "")[:10]
        usd = row.get("usd")
        if row_day and row_day != day and isinstance(usd, (int, float)) and usd > 0:
            priors.append(float(usd))
    if not priors:
        return 0, None
    return len(priors), max(priors)


def should_push(
    latest: dict[str, Any],
    rows: list[Any],
    baselined: bool,
) -> tuple[bool, str]:
    day = str(latest.get("time") or "")[:10]
    usd = float(latest["usd"])
    n_prior, peak = prior_peak(rows, day)
    if not baselined or n_prior < 1:
        return False, "baseline"
    dod = latest.get("dod_pct")
    if isinstance(dod, (int, float)) and dod >= DOD_PCT:
        return True, "dod"
    chg7 = latest.get("chg_7d_pct")
    if isinstance(chg7, (int, float)) and chg7 >= CHG7D_PCT:
        return True, "chg7d"
    if n_prior >= ATH_MIN_DAYS - 1 and peak is not None and usd > peak * 1.0005:
        return True, "ath"
    return False, "quiet"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    path = resolve_json(args.json)
    doc = json.loads(path.read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc.get("latest"), dict) else None
    rows = doc.get("data") if isinstance(doc.get("data"), list) else []
    if not latest and rows:
        latest = rows[-1]
    if not isinstance(latest, dict) or not isinstance(latest.get("usd"), (int, float)):
        print("[bnb-rwa] 无最新市值")
        return 0

    day = str(latest.get("time") or "")[:10]
    state = load_state()
    ok, reason = should_push(latest, rows if isinstance(rows, list) else [], bool(state.get("baselined")))
    n_prior, peak = prior_peak(rows if isinstance(rows, list) else [], day)
    print(
        f"[bnb-rwa] json={path} day={day} usd={float(latest['usd']):.0f} "
        f"prior_n={n_prior} prior_peak={peak} dod={latest.get('dod_pct')} "
        f"chg7d={latest.get('chg_7d_pct')} reason={reason} baselined={state.get('baselined')}"
    )

    if not ok and not args.force:
        print(f"[skip] {reason}，不发")
        if args.push:
            state["baselined"] = True
            state["lastDay"] = day
            state["lastUsd"] = float(latest["usd"])
            save_state(state)
        return 0

    news_reason = reason if ok else "force"
    text = build_news(latest, news_reason)
    print("---")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    msg_id = f"bnb-rwa-{reason}-{day}"
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    send_lark(webhook, secret, text)
    print("Lark OK", msg_id)
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    state["baselined"] = True
    state["lastDay"] = day
    state["lastUsd"] = float(latest["usd"])
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
