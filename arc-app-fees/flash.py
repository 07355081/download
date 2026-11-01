# -*- coding: utf-8 -*-
"""Arc 链上应用手续费（DefiLlama 近似）→ Lark。

口径：overview/fees 应用费，不是网络 gas / Dune。

- 日常：【实时日常报告】最新完整日 Arc 应用费、相对上线首日跌幅、对照链
- 阈值：【历史阈值警报】Arc 日环比过线 / 相对首日跌幅刚越过档位
- 特殊：【特殊警报】五链应用费排序变化（焦点第一易主）
- --history-scan：回扫未推送阈值/特殊

用法：
  python flash.py
  python flash.py --push
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "arc_app_fees.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "arc-app-fees" / "arc_app_fees.json",
    Path("/root/dashboard/public/json/arc-app-fees/arc_app_fees.json"),
]
STATE_PATH = HERE / "output" / "arc_app_fees_lark.json"
LOG_PATH = HERE / "arc_app_fees_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/arc-app-fees"

DEFAULT_ARC_DOD_PCT = 25.0
DEFAULT_VS_LAUNCH_DROP_PCT = 80.0  # 相对首日跌幅绝对值达到此值（跌）触发
HISTORY_SCAN_DAYS = 45
DEFAULT_HISTORY_LIMIT = 4

PEER_KEYS = ("robinhood_chain", "base", "arbitrum", "sui")
PEER_LABEL = {
    "robinhood_chain": "Robinhood Chain",
    "base": "Base",
    "arbitrum": "Arbitrum",
    "sui": "Sui",
}
RANK_KEYS = ("arc",) + PEER_KEYS
RANK_LABEL = {"arc": "Arc", **PEER_LABEL}


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
    for key in (
        "LARK_ALERT_WEBHOOK",
        "LARK_ALERT_SECRET",
        "ARC_APP_FEES_DOD_PCT",
        "ARC_APP_FEES_VS_LAUNCH_DROP_PCT",
    ):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_json_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 arc_app_fees.json，请先跑 download.py")


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("history"), list):
        raise RuntimeError("invalid json")
    return doc


def hist_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("history") or []:
        if isinstance(row, dict) and row.get("time"):
            out[str(row["time"])[:10]] = row
    return out


def closed_days(doc: dict[str, Any], today: str) -> list[str]:
    return sorted(d for d in hist_map(doc) if d < today and "arc" in hist_map(doc)[d])


def fmt_usd(n: float) -> str:
    a = abs(n)
    if a >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    if a >= 1e4:
        return f"{n / 1e4:.1f} 万美元"
    if a >= 1000:
        return f"{n:,.0f} 美元"
    return f"{n:.0f} 美元"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def cn_day(day: str) -> str:
    _y, m, d = day.split("-")
    return f"{int(m)} 月 {int(d)} 日"


def pct_change(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (curr - prev) / prev * 100.0


def vs_launch(doc: dict[str, Any], day: str) -> float | None:
    launch = str(doc.get("launch_day") or "2026-09-16")
    hmap = hist_map(doc)
    launch_v = hmap.get(launch, {}).get("arc")
    cur = hmap.get(day, {}).get("arc")
    if not isinstance(launch_v, (int, float)) or not isinstance(cur, (int, float)):
        return None
    return pct_change(float(cur), float(launch_v))


def prev_day(day: str) -> str | None:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - timedelta(days=1)).isoformat()


def build_core(doc: dict[str, Any], day: str) -> str:
    hmap = hist_map(doc)
    row = hmap.get(day) or {}
    arc = float(row.get("arc") or 0)
    launch = str(doc.get("launch_day") or "2026-09-16")
    drop = vs_launch(doc, day)
    peer_parts = []
    for key in PEER_KEYS:
        val = row.get(key)
        if isinstance(val, (int, float)):
            peer_parts.append(f"{PEER_LABEL[key]} 约 {fmt_usd(float(val))}")
    peers = "、".join(peer_parts) if peer_parts else "对照链暂缺"

    drop_phrase = ""
    if drop is not None:
        if drop < 0:
            drop_phrase = f"，较上线首日（{cn_day(launch)}）下降 {abs(drop):.1f}%"
        else:
            drop_phrase = f"，较上线首日（{cn_day(launch)}）变化 {fmt_pct(drop)}"

    tops = doc.get("top_protocols_24h") or []
    top_phrase = ""
    if isinstance(tops, list) and tops and day == str(doc.get("as_of") or ""):
        names = "、".join(str(t.get("name") or "?") for t in tops[:3] if isinstance(t, dict))
        if names:
            top_phrase = f"近 24 小时应用费居前协议包括 {names}。"

    return (
        f"据吴说数据中心（DefiLlama 链上应用手续费口径）统计，截至 {cn_day(day)}，"
        f"Arc 链上应用手续费合计约 {fmt_usd(arc)}{drop_phrase}；"
        f"同日对照链应用费：{peers}。"
        f"{top_phrase}"
        "说明：本口径为该链上各协议应用/协议手续费合计，不是网络 gas，"
        "与 Dune 网络手续费外电数字会有明显差异。"
        f"\n{DASHBOARD_URL}"
    )


def with_mark(mark: str, text: str) -> str:
    if text.startswith(mark):
        return text
    return f"{mark}\n{text}"


def evaluate_threshold(
    doc: dict[str, Any],
    day: str,
    *,
    dod_line: float,
    drop_line: float,
) -> tuple[bool, str]:
    hmap = hist_map(doc)
    cur = hmap.get(day)
    if not cur or not isinstance(cur.get("arc"), (int, float)):
        return False, "无 Arc 当日数据"
    reasons: list[str] = []
    arc = float(cur["arc"])
    pd = prev_day(day)
    if pd and pd in hmap and isinstance(hmap[pd].get("arc"), (int, float)):
        dod = pct_change(arc, float(hmap[pd]["arc"]))
        if dod is not None and abs(dod) >= dod_line:
            reasons.append(f"Arc 应用费日环比 {fmt_pct(dod)}（线 {dod_line:g}%）")

    drop = vs_launch(doc, day)
    prev_drop = vs_launch(doc, pd) if pd else None
    # 相对首日跌幅：当日跌幅绝对值刚达到/越过 drop_line（从更浅跌到更深，或首次过线）
    if drop is not None and drop <= -drop_line:
        crossed = prev_drop is None or prev_drop > -drop_line
        if crossed:
            reasons.append(
                f"Arc 应用费相对上线首日跌幅达 {abs(drop):.1f}%（线 {drop_line:g}%）"
            )

    return bool(reasons), "；".join(reasons) if reasons else "未过线"


def evaluate_special(doc: dict[str, Any], day: str) -> tuple[bool, str, str]:
    hmap = hist_map(doc)
    cur = hmap.get(day)
    pd = prev_day(day)
    if not cur or not pd or pd not in hmap:
        return False, "", ""

    def rank(row: dict[str, Any]) -> list[str]:
        items = []
        for k in RANK_KEYS:
            v = row.get(k)
            if isinstance(v, (int, float)):
                items.append((k, float(v)))
        items.sort(key=lambda x: x[1], reverse=True)
        return [k for k, _ in items]

    cur_r, prev_r = rank(cur), rank(hmap[pd])
    if not cur_r or not prev_r or cur_r == prev_r:
        return False, "", ""
    if cur_r[0] == prev_r[0]:
        # 仅内部位次变化不算特殊，要求第一易主
        return False, "", ""
    why = (
        f"五链应用费第一由 {RANK_LABEL.get(prev_r[0], prev_r[0])} "
        f"换为 {RANK_LABEL.get(cur_r[0], cur_r[0])}"
    )
    return True, why, f"top1-{day}"


def gen_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = gen_sign(secret, ts)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [str(x) for x in ids] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def mark_for(kind: str) -> str:
    if kind == "special":
        return LARK_MARK_SPECIAL
    if kind == "threshold":
        return LARK_MARK_THRESHOLD
    return LARK_MARK_DAILY


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--json", default="")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    dod_line = env_float(env, "ARC_APP_FEES_DOD_PCT", DEFAULT_ARC_DOD_PCT)
    drop_line = env_float(env, "ARC_APP_FEES_VS_LAUNCH_DROP_PCT", DEFAULT_VS_LAUNCH_DROP_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        doc = load_doc(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    today = datetime.now(TZ_CN).date().isoformat()
    days = closed_days(doc, today)
    if not days:
        print("[ERROR] 无已闭合日", file=sys.stderr)
        return 1
    latest = days[-1]
    print(
        f"[arc-app-fees] json={json_path} latest={latest} "
        f"dod={dod_line:g}% vs_launch_drop={drop_line:g}%"
    )

    briefs: list[dict[str, Any]] = []

    hit_s, why_s, suf = evaluate_special(doc, latest)
    if hit_s and suf:
        briefs.append(
            {
                "id": f"arc-app-fees-special-{suf}",
                "kind": "special",
                "text": f"Arc 与对照链应用费结构出现特殊变化：{why_s}。" + build_core(doc, latest),
            }
        )

    hit_t, why_t = evaluate_threshold(doc, latest, dod_line=dod_line, drop_line=drop_line)
    if hit_t:
        briefs.append(
            {
                "id": f"arc-app-fees-threshold-{latest}",
                "kind": "threshold",
                "text": f"Arc 链上应用手续费出现超阈值变动：{why_t}。" + build_core(doc, latest),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"arc-app-fees-daily-{latest}",
                "kind": "daily",
                "text": build_core(doc, latest),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for day in reversed(days[:-1][-HISTORY_SCAN_DAYS:]):
            if hist_n >= limit:
                break
            ok_s, why, suf = evaluate_special(doc, day)
            if ok_s and suf:
                msg_id = f"arc-app-fees-special-{suf}"
                if msg_id not in sent_set or args.force:
                    briefs.append(
                        {
                            "id": msg_id,
                            "kind": "special",
                            "text": (
                                "（历史回扫，不是当日例行连推）"
                                f"Arc 与对照链应用费结构出现特殊变化：{why}。"
                                + build_core(doc, day)
                            ),
                        }
                    )
                    hist_n += 1
                    if hist_n >= limit:
                        break
            ok_t, why_t = evaluate_threshold(doc, day, dod_line=dod_line, drop_line=drop_line)
            if not ok_t:
                continue
            msg_id = f"arc-app-fees-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "text": (
                        "（历史回扫，不是当日例行连推）"
                        f"Arc 链上应用手续费出现超阈值变动：{why_t}。"
                        + build_core(doc, day)
                    ),
                }
            )
            hist_n += 1

    order = {"special": 0, "threshold": 1, "daily": 2}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in sorted(briefs, key=lambda b: (order.get(b["kind"], 9), b["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)

    for brief in unique:
        text = with_mark(mark_for(brief["kind"]), brief["text"])
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} already={brief['id'] in sent_set}")
        print(text)

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in unique:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"skip {brief['id']}")
            continue
        text = with_mark(mark_for(brief["kind"]), brief["text"])
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text, extra=f"err={exc}")
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
        print(f"[arc-app-fees] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
