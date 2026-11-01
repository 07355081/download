# -*- coding: utf-8 -*-
"""分链稳定币市值：ETH 占比 + 近 30 日新增 → Lark。

口径：DefiLlama 稳定币分链流通市值（不含 RWA），不是 Token Terminal「RWA+稳定币」合并数。
HyperEVM 在 DefiLlama 无独立链名，本脚本用 Hyperliquid L1，不能与新闻里的 HyperEVM 直接对表。

- 日常：每个数据日一条【实时日常报告】（ETH 占比 + Top 链 30 日增量）
- 阈值：单链 30 日增量 ≥ 线，或 ETH 占比日变动 |Δ| ≥ 线 →【历史阈值警报】
- 历史回扫：近 N 个数据日破阈且未发过的，用特殊抬头并注明回扫

用法：
  python chain_mcap_flash.py
  python chain_mcap_flash.py --push
  python chain_mcap_flash.py --push --history-scan
  python chain_mcap_flash.py --push --force
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "stablecoins_marketcap.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "stablecoin" / "stablecoins_marketcap.json",
    Path("/root/dashboard/public/json/stablecoin/stablecoins_marketcap.json"),
]
STATE_PATH = HERE / "output" / "chain_mcap_lark.json"
LOG_PATH = HERE / "chain_mcap_flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/stablecoin-marketcap"

LOOKBACK_DAYS = 30
DEFAULT_CHAIN_30D_USD = 400_000_000.0  # 4 亿美元
DEFAULT_ETH_SHARE_DOD_PP = 0.8  # 百分点
HISTORY_SCAN_DAYS = 45
DEFAULT_HISTORY_LIMIT = 5  # 单次历史回扫最多推几条，防刷屏

CHAIN_LABELS: dict[str, str] = {
    "aptos": "Aptos",
    "arbitrum": "Arbitrum",
    "arc": "Arc",
    "avalanche": "Avalanche",
    "base": "Base",
    "bsc": "BSC",
    "ethereum": "Ethereum",
    "hyperliquid_l1": "Hyperliquid L1",
    "mantle": "Mantle",
    "op_mainnet": "Optimism",
    "plasma": "Plasma",
    "polygon": "Polygon",
    "solana": "Solana",
    "tempo": "Tempo",
    "ton": "TON",
    "tron": "TRON",
    "x_layer": "X Layer",
}


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
        "STABLECOIN_CHAIN_30D_USD",
        "STABLECOIN_ETH_SHARE_DOD_PP",
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
            raise FileNotFoundError(f"指定 JSON 不存在: {path}")
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 stablecoins_marketcap.json")


def load_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        data = doc.get("data")
    else:
        data = doc
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{path} 无 data 行")
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        if len(day) != 10:
            continue
        item: dict[str, Any] = {"time": day}
        for key, value in row.items():
            if key == "time":
                continue
            if isinstance(value, (int, float)):
                item[key] = float(value)
        rows.append(item)
    rows.sort(key=lambda r: str(r["time"]))
    if not rows:
        raise RuntimeError(f"{path} 无有效日行")
    return rows


def chain_prefix(col: str) -> str | None:
    if col.endswith("_stablecoins") and not col.startswith("all_"):
        return col[: -len("_stablecoins")]
    return None


def label_chain(prefix: str) -> str:
    return CHAIN_LABELS.get(prefix, prefix.replace("_", " ").title())


def fmt_yi(usd: float) -> str:
    """亿美元，保留合适位数。"""
    yi = usd / 1e8
    if abs(yi) >= 100:
        return f"{yi:.0f} 亿美元"
    if abs(yi) >= 10:
        return f"{yi:.1f} 亿美元"
    return f"{yi:.2f} 亿美元"


def fmt_pp(pp: float) -> str:
    sign = "+" if pp >= 0 else ""
    return f"{sign}{pp:.2f} 个百分点"


def find_row_on_or_before(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    hit: dict[str, Any] | None = None
    for row in rows:
        if str(row["time"]) <= day:
            hit = row
        else:
            break
    return hit


def row_by_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row["time"]) == day:
            return row
    return None


def eth_share(row: dict[str, Any]) -> float | None:
    total = row.get("all_stablecoins")
    eth = row.get("ethereum_stablecoins")
    if not isinstance(total, (int, float)) or not isinstance(eth, (int, float)):
        return None
    if total <= 0:
        return None
    return float(eth) / float(total) * 100.0


def chain_deltas_30d(
    rows: list[dict[str, Any]], day: str
) -> list[tuple[str, float, float, float]]:
    """Return [(prefix, delta, now, prev), ...] sorted by delta desc."""
    curr = row_by_day(rows, day)
    if not curr:
        return []
    end = date.fromisoformat(day)
    target = (end - timedelta(days=LOOKBACK_DAYS)).isoformat()
    prev = find_row_on_or_before(rows, target)
    if not prev:
        return []
    out: list[tuple[str, float, float, float]] = []
    keys = {k for k in curr if chain_prefix(k)} | {k for k in prev if chain_prefix(k)}
    for col in keys:
        prefix = chain_prefix(col)
        if not prefix:
            continue
        a = curr.get(col)
        b = prev.get(col)
        if not isinstance(a, (int, float)):
            continue
        b_val = float(b) if isinstance(b, (int, float)) else 0.0
        out.append((prefix, float(a) - b_val, float(a), b_val))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def evaluate_threshold(
    rows: list[dict[str, Any]],
    day: str,
    *,
    chain_line: float,
    share_dod_pp: float,
) -> tuple[bool, list[str], list[tuple[str, float]]]:
    """单链 30 日增量破线：仅在「刚越过」当日报警（前一日同口径未破线），避免连日刷屏。"""
    reasons: list[str] = []
    hot: list[tuple[str, float]] = []
    idx = next((i for i, r in enumerate(rows) if str(r["time"]) == day), None)
    if idx is None:
        return False, [], []

    deltas = chain_deltas_30d(rows, day)
    prev_day = str(rows[idx - 1]["time"]) if idx > 0 else None
    prev_over: set[str] = set()
    if prev_day:
        for prefix, delta, _now, _prev in chain_deltas_30d(rows, prev_day):
            if delta >= chain_line:
                prev_over.add(prefix)

    for prefix, delta, _now, _prev in deltas:
        if delta < chain_line:
            continue
        if prefix in prev_over:
            continue  # 已在破线区间内，不算新触发
        reasons.append(
            f"{label_chain(prefix)} 近 {LOOKBACK_DAYS} 日稳定币市值增加 {fmt_yi(delta)}，"
            f"刚越过阈值（线 {fmt_yi(chain_line)}）"
        )
        hot.append((prefix, delta))

    curr = rows[idx]
    if idx > 0:
        prev = rows[idx - 1]
        s0 = eth_share(prev)
        s1 = eth_share(curr)
        if s0 is not None and s1 is not None:
            dod = s1 - s0
            if abs(dod) >= share_dod_pp:
                reasons.append(
                    f"以太坊稳定币市值占比由 {s0:.2f}% 变为 {s1:.2f}%（{fmt_pp(dod)}，线 ±{share_dod_pp:g} 个百分点）"
                )
    return bool(reasons), reasons, hot


def build_daily_text(rows: list[dict[str, Any]], day: str) -> str:
    curr = row_by_day(rows, day)
    assert curr is not None
    total = float(curr.get("all_stablecoins") or 0)
    eth = float(curr.get("ethereum_stablecoins") or 0)
    share = eth_share(curr)
    share_s = f"{share:.1f}%" if share is not None else "—"
    deltas = chain_deltas_30d(rows, day)[:5]
    top_bits = []
    for prefix, delta, now, _prev in deltas:
        sign = "+" if delta >= 0 else ""
        top_bits.append(
            f"{label_chain(prefix)} {sign}{fmt_yi(delta)}（现约 {fmt_yi(now)}）"
        )
    top_s = "；".join(top_bits) if top_bits else "—"
    y, m, d = day.split("-")
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"全市场稳定币流通市值约 {fmt_yi(total)}，其中以太坊约 {fmt_yi(eth)}，占比约 {share_s}。"
        f"近 {LOOKBACK_DAYS} 日分链新增市值靠前的是：{top_s}。"
        f"口径为 DefiLlama 稳定币分链流通市值，不含 RWA，与 Token Terminal「RWA+稳定币」合并数不可直接对表；"
        f"Hyperliquid L1 亦不能等同新闻中的 HyperEVM。"
        f"\n{DASHBOARD_URL}"
    )
    title = f"稳定币分链市值：以太坊占比约 {share_s}"
    return f"{title}\n{body}"


def build_threshold_text(
    rows: list[dict[str, Any]],
    day: str,
    reasons: list[str],
    *,
    historical: bool,
) -> str:
    curr = row_by_day(rows, day)
    share = eth_share(curr) if curr else None
    share_s = f"{share:.1f}%" if share is not None else "—"
    total = float((curr or {}).get("all_stablecoins") or 0)
    y, m, d = day.split("-")
    title = f"稳定币分链市值异动：{int(m)} 月 {int(d)} 日触发阈值"
    body = (
        f"吴说获悉，DefiLlama 稳定币分链数据在 {int(y)} 年 {int(m)} 月 {int(d)} 日出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日全市场稳定币约 {fmt_yi(total)}，以太坊占比约 {share_s}。"
        f"本条为阈值异动提醒，不是例行日报；口径不含 RWA。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def with_mark(mark: str, text: str) -> str:
    return f"{mark}\n{text}"


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
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    if not isinstance(doc, dict):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str(doc.get("updatedAt") or ""),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true", help="扫近若干数据日发未推送的阈值条")
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help=f"单次历史回扫最多推几条（默认 {DEFAULT_HISTORY_LIMIT}）",
    )
    parser.add_argument("--json", default="", help="覆盖 JSON 路径")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    chain_line = env_float(env, "STABLECOIN_CHAIN_30D_USD", DEFAULT_CHAIN_30D_USD)
    share_dod = env_float(env, "STABLECOIN_ETH_SHARE_DOD_PP", DEFAULT_ETH_SHARE_DOD_PP)

    try:
        json_path = resolve_json_path(args.json or None)
        rows = load_rows(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = str(rows[-1]["time"])
    print(
        f"[chain-mcap] json={json_path} latest={latest} rows={len(rows)} "
        f"line_30d={chain_line:g} share_dod_pp={share_dod:g}"
    )

    briefs: list[dict[str, Any]] = []

    hit, reasons, _hot = evaluate_threshold(
        rows, latest, chain_line=chain_line, share_dod_pp=share_dod
    )
    if hit:
        briefs.append(
            {
                "id": f"stablecoin-chain-threshold-{latest}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(rows, latest, reasons, historical=False),
            }
        )
    briefs.append(
        {
            "id": f"stablecoin-chain-daily-{latest}",
            "kind": "daily",
            "historical": False,
            "text": build_daily_text(rows, latest),
        }
    )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        days = [str(r["time"]) for r in rows]
        scan_days = days[-HISTORY_SCAN_DAYS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for day in reversed(scan_days):
            if day == latest:
                continue
            if hist_n >= limit:
                break
            msg_id = f"stablecoin-chain-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            ok, why, _ = evaluate_threshold(
                rows, day, chain_line=chain_line, share_dod_pp=share_dod
            )
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold_text(rows, day, why, historical=True),
                }
            )
            hist_n += 1

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    unique.sort(key=lambda b: (0 if b["kind"] == "threshold" else 1, b["id"]))
    for brief in unique:
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        already = brief["id"] in state["sentIds"]
        print("---")
        print(
            f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} already={already}"
        )
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
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
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
        state["sentIds"] = state["sentIds"][-120:]
        save_state(state)
        print(f"[chain-mcap] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
