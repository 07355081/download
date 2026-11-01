"""OKX 储备金证明 → 飞书（正式新闻体）。

- 日常：新 audit_id / 新 snapshot_date →【实时日常报告】（月更）
- 阈值：用户持仓变动或储备率过线 →【历史阈值警报】
  默认：BTC |Δ用户| ≥ 5000；ETH |Δ用户| ≥ 100000；USDT |Δ用户| ≥ 3e8；
       或 BTC/ETH/USDT/USDC 任一储备率 < 100%
  环境变量：OKX_POR_BTC_USER_DELTA / OKX_POR_ETH_USER_DELTA /
           OKX_POR_USDT_USER_DELTA / OKX_POR_MIN_RATIO

用法：
  python flash.py
  python flash.py --refresh
  python flash.py --refresh --push
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
OUT_DIR = HERE / "output" / "json"
LATEST_PATH = OUT_DIR / "okx_por_latest.json"
HISTORY_DIR = OUT_DIR / "okx-por-history"
STATE_PATH = HERE / "output" / "okx_por_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
POR_PAGE = "https://data.wublock123.com/dashboard/lark-feeds/okx-por"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
FOCUS = ("BTC", "ETH", "USDT", "USDC")
HTTP_TIMEOUT = 45

DEFAULT_BTC_USER_DELTA = 5_000.0
DEFAULT_ETH_USER_DELTA = 100_000.0
DEFAULT_USDT_USER_DELTA = 300_000_000.0
DEFAULT_MIN_RATIO = 1.0


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

def load_env(path: Path) -> dict[str, str]:
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
        env.update(load_env(path))
    for k in (
        "LARK_ALERT_WEBHOOK",
        "LARK_ALERT_SECRET",
        "OKX_POR_BTC_USER_DELTA",
        "OKX_POR_ETH_USER_DELTA",
        "OKX_POR_USDT_USER_DELTA",
        "OKX_POR_MIN_RATIO",
    ):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return
    parsed = json.loads(body)
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"Lark rejected: {body[:200]}")


def with_mark(mark: str, text: str) -> str:
    body = text.lstrip("\ufeff")
    if body.startswith(mark):
        return body
    return f"{mark}\n{body}"


def month_label_zh(iso_date: str) -> str:
    y, m, d = iso_date.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def fmt_ratio_pct(ratio: float) -> str:
    pct = ratio * 100.0
    text = f"{pct:.2f}".rstrip("0").rstrip(".")
    if "." not in text:
        text = f"{pct:.2f}"
    return f"{text}%"


def fmt_coins(amount: float, coin: str) -> str:
    n = abs(float(amount))
    if coin in ("USDT", "USDC", "DOGE"):
        if n >= 1e8:
            return f"{n / 1e8:.2f} 亿枚"
        if n >= 1e4:
            return f"{n / 1e4:.2f} 万枚"
        return f"{n:,.2f} 枚"
    if n >= 1e4:
        text = f"{n / 1e4:.2f}".rstrip("0").rstrip(".")
        return f"{text} 万枚"
    text = f"{n:,.3f}".rstrip("0").rstrip(".")
    return f"{text} 枚"


def fmt_pct_change(curr: float, prev: float) -> str:
    if prev == 0:
        return ""
    pct = (curr - prev) / abs(prev) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"（{sign}{pct:.2f}%）"


def prev_history_doc(current_date: str) -> dict[str, Any] | None:
    if not HISTORY_DIR.is_dir():
        return None
    dates = sorted(p.stem for p in HISTORY_DIR.glob("????-??-??.json") if p.stem < current_date)
    if not dates:
        return None
    try:
        doc = load_json(HISTORY_DIR / f"{dates[-1]}.json")
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def asset(doc: dict[str, Any], coin: str) -> dict[str, Any] | None:
    assets = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    row = assets.get(coin)
    return row if isinstance(row, dict) else None


def build_daily_text(curr: dict[str, Any], prev: dict[str, Any] | None) -> str:
    snap = str(curr.get("snapshot_date") or "")
    label = month_label_zh(snap) if snap else "本期"
    audit = str(curr.get("audit_id") or "")
    bits = []
    for coin in ("BTC", "ETH", "USDT"):
        row = asset(curr, coin)
        if not row:
            continue
        line = f"用户 {coin} 资产约为 {fmt_coins(float(row['user']), coin)}"
        if prev:
            old = asset(prev, coin)
            if old and old.get("user") is not None:
                delta = float(row["user"]) - float(old["user"])
                verb = "增加" if delta >= 0 else "减少"
                pct = fmt_pct_change(float(row["user"]), float(old["user"]))
                line += f"，较上次{verb} {fmt_coins(abs(delta), coin)}{pct}"
        ratio = row.get("ratio")
        if isinstance(ratio, (int, float)):
            line += f"；储备金率 {fmt_ratio_pct(float(ratio))}"
        bits.append(line)
    body = (
        f"吴说获悉，OKX 公布资产储备证明（Proof of Reserves，快照日期 {label}"
        f"{f'，报告 ID {audit}' if audit and not audit.startswith('OKX-') else ''}）。"
        + "；".join(bits)
        + "。"
    )
    body += (
        "储备金率反映特定快照时点平台钱包资产相对用户账户资产的覆盖比例，"
        "并不等同于 OKX 自有资产规模；第三方托管余额需向托管机构核实。"
    )
    return body + f"\n\n{POR_PAGE}"


def evaluate_threshold(
    curr: dict[str, Any],
    prev: dict[str, Any] | None,
    *,
    btc_delta: float,
    eth_delta: float,
    usdt_delta: float,
    min_ratio: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for coin in FOCUS:
        row = asset(curr, coin)
        if not row or row.get("ratio") is None:
            continue
        ratio = float(row["ratio"])
        if ratio < min_ratio:
            reasons.append(f"{coin} 储备率 {fmt_ratio_pct(ratio)} < {fmt_ratio_pct(min_ratio)}")
    if prev:
        lines = (("BTC", btc_delta), ("ETH", eth_delta), ("USDT", usdt_delta))
        for coin, line in lines:
            a = asset(curr, coin)
            b = asset(prev, coin)
            if not a or not b or a.get("user") is None or b.get("user") is None:
                continue
            delta = float(a["user"]) - float(b["user"])
            if abs(delta) >= line:
                reasons.append(f"用户 {coin} 持仓变动 {delta:+,.3f}（线 ±{line:g}）")
    return bool(reasons), reasons


def build_threshold_text(
    curr: dict[str, Any],
    prev: dict[str, Any] | None,
    reasons: list[str],
    *,
    historical: bool,
) -> str:
    snap = str(curr.get("snapshot_date") or "")
    label = month_label_zh(snap) if snap else "本期"
    title = f"OKX 储备金证明异动：{label}快照触发阈值"
    body = (
        "不是日常新闻，是历史阈值警报。\n"
        f"吴说获悉，OKX {label}储备金证明出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
    )
    bits = []
    for coin in FOCUS:
        row = asset(curr, coin)
        if row and row.get("ratio") is not None:
            bits.append(f"{coin} {fmt_ratio_pct(float(row['ratio']))}")
    if bits:
        body += "当期储备率：" + "、".join(bits) + "。"
    if prev:
        for coin in ("BTC", "ETH", "USDT"):
            a = asset(curr, coin)
            b = asset(prev, coin)
            if a and b and a.get("user") is not None and b.get("user") is not None:
                delta = float(a["user"]) - float(b["user"])
                verb = "增加" if delta >= 0 else "减少"
                body += f"用户 {coin} 持仓较上期{verb} {fmt_coins(abs(delta), coin)}。"
    body += "本条为阈值异动提醒，不是月度例行简报。"
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（快照日 {snap}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += "储备金率为快照时点覆盖比例，并不等同于 OKX 自有资产规模。"
    return f"{title}\n{body}\n\n{POR_PAGE}"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = load_json(STATE_PATH)
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    if not isinstance(doc, dict):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str(doc.get("updatedAt") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    btc_line = env_float(env, "OKX_POR_BTC_USER_DELTA", DEFAULT_BTC_USER_DELTA)
    eth_line = env_float(env, "OKX_POR_ETH_USER_DELTA", DEFAULT_ETH_USER_DELTA)
    usdt_line = env_float(env, "OKX_POR_USDT_USER_DELTA", DEFAULT_USDT_USER_DELTA)
    min_ratio = env_float(env, "OKX_POR_MIN_RATIO", DEFAULT_MIN_RATIO)

    if args.refresh:
        import download as por_download

        rc = por_download.main()
        if rc != 0:
            return rc

    if not LATEST_PATH.is_file():
        print(f"[ERROR] 缺少 {LATEST_PATH}，请先 --refresh", file=sys.stderr)
        return 1

    curr = load_json(LATEST_PATH)
    if not isinstance(curr, dict):
        print("[ERROR] latest JSON 格式异常", file=sys.stderr)
        return 1
    snap = str(curr.get("snapshot_date") or "")
    audit = str(curr.get("audit_id") or snap or "unknown")
    prev = prev_history_doc(snap) if snap else None

    briefs: list[dict[str, Any]] = []
    hit, reasons = evaluate_threshold(
        curr,
        prev,
        btc_delta=btc_line,
        eth_delta=eth_line,
        usdt_delta=usdt_line,
        min_ratio=min_ratio,
    )
    if hit:
        briefs.append(
            {
                "id": f"okx-por-threshold-{audit}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(curr, prev, reasons, historical=False),
            }
        )
    briefs.append(
        {
            "id": f"okx-por-daily-{audit}",
            "kind": "daily",
            "historical": False,
            "text": build_daily_text(curr, prev),
        }
    )

    if args.history_scan and HISTORY_DIR.is_dir():
        for path in sorted(HISTORY_DIR.glob("????-??-??.json"), reverse=True)[:24]:
            if path.stem == snap:
                continue
            try:
                doc = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            p2 = prev_history_doc(path.stem)
            ok, why = evaluate_threshold(
                doc,
                p2,
                btc_delta=btc_line,
                eth_delta=eth_line,
                usdt_delta=usdt_line,
                min_ratio=min_ratio,
            )
            if not ok:
                continue
            aid = str(doc.get("audit_id") or path.stem)
            briefs.append(
                {
                    "id": f"okx-por-threshold-{aid}",
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold_text(doc, p2, why, historical=True),
                }
            )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    unique.sort(key=lambda b: (0 if b["kind"] == "threshold" else 1, b["id"]))

    state = load_state()
    print(
        f"[okx-por] audit={audit} snap={snap} prev={prev.get('snapshot_date') if prev else None} "
        f"threshold_hit={hit} briefs={len(unique)} "
        f"lines: BTC±{btc_line:g} ETH±{eth_line:g} USDT±{usdt_line:g} ratio<{min_ratio:g}"
    )
    for brief in unique:
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        already = brief["id"] in state["sentIds"]
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} already={already}")
        print(text)

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

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
            stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(f"=== {stamp} {brief['id']} ok=False err={exc}\n{text}\n\n")
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"=== {stamp} {brief['id']} ok=True\n{text}\n\n")
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-60:]
        state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_json(STATE_PATH, state)
        print(f"[okx-por] state saved → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
