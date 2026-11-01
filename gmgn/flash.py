"""生成并推送「GMGN 主要 Meme 市场 24h 成交」快讯到飞书。

默认只打印（dry-run）；加 --push 才发群。

双通道（与 defi-tvl / stablecoin 一致）：
  - 【实时日常报告】：每个上海自然日成功推送最多一条（例行日报）
  - 【历史阈值警报】：过线时另发一条（与日常分开；同日阈值最多一条）
    · 多链合计 |日环比| ≥ GMGN_DOD_PCT（默认 15%）
    · 或任一链 |日环比| ≥ GMGN_CHAIN_DOD_PCT（默认 25%）
    · 可选：合计成交额 ≥ GMGN_VOLUME_USD（默认 0=关闭）

数据：优先读本模块最新 JSON；加 --refresh 则先跑 download。
可用 --daily-only / --threshold-only 只跑其中一路；--force 忽略日去重。

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
ROOT_DIR = HERE.parent
JSON_PATH = HERE / "output" / "json" / "meme_markets_24h.json"
LOG_PATH = HERE / "flash.log"
DEFAULT_ENV = ROOT_DIR / ".env"
ALT_ENVS = [
    Path("/root/dashboard/.env.live"),
    ROOT_DIR.parent / "data-dashboard" / ".env.live",
    ROOT_DIR.parent / "data-dashboard" / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
YI = 1e8
HTTP_TIMEOUT = 15
DASHBOARD_URL = "https://data.wublock123.com/dashboard/meme-markets"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
SEND_GAP_SEC = 0.5

# 默认阈值：与 ETF「过线才推」同思路；可用环境变量覆盖。
DEFAULT_DOD_PCT = 15.0
DEFAULT_CHAIN_DOD_PCT = 25.0
DEFAULT_VOLUME_USD = 0.0  # 0=不按绝对成交额触发

CHAIN_LABEL = {
    "sol": "Solana",
    "robinhood": "Robinhood",
    "bsc": "BNB",
    "arc": "Arc",
    "eth": "以太坊",
}

# 正文链顺序（与常见吴说稿一致）；未列出的链按成交量追加在后
CHAIN_ORDER = ("robinhood", "sol", "bsc", "arc", "eth")


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
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def merge_env(paths: list[Path]) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        env.update(load_env_file(path))
    for k in (
        "GMGN_API_KEY",
        "GMGN_DOD_PCT",
        "GMGN_CHAIN_DOD_PCT",
        "GMGN_VOLUME_USD",
        "LARK_ALERT_WEBHOOK",
        "LARK_ALERT_SECRET",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
    ):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def apply_env(env: dict[str, str]) -> None:
    for k, v in env.items():
        if k and v and k not in os.environ:
            os.environ[k] = v


def _env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def fmt_usd(v: float) -> str:
    if v >= YI:
        return f"{v / YI:.2f} 亿美元"
    return f"{v / 1e4:,.0f} 万美元"


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def log_has(needle: str) -> bool:
    if not needle or not LOG_PATH.exists():
        return False
    return needle in LOG_PATH.read_text(encoding="utf-8", errors="replace")


def already_pushed_daily(day: str) -> bool:
    """同一自然日日常报告成功推送过则跳过。"""
    if not day:
        return False
    # 新格式
    if log_has(f"day={day} kind=daily ok=True"):
        return True
    return False


def already_pushed_threshold(day: str) -> bool:
    """同一自然日阈值警报成功推送过则跳过（兼容旧日志行）。"""
    if not day:
        return False
    if log_has(f"day={day} kind=threshold ok=True"):
        return True
    # 旧版只推阈值时的日志
    if log_has(f"day={day} threshold=hit ok=True"):
        return True
    return False


def evaluate_threshold(
    snap: dict[str, Any],
    *,
    dod_pct_min: float,
    chain_dod_pct_min: float,
    volume_usd_min: float,
) -> dict[str, Any]:
    """返回 {hit, reasons, tip}。无日环比且未开成交额线 → 不推阈值。"""
    reasons: list[str] = []
    total = float(snap.get("total_volume") or 0)
    dod = snap.get("dod_pct")
    chains = [c for c in (snap.get("chains") or []) if isinstance(c, dict)]

    if volume_usd_min > 0 and total >= volume_usd_min:
        reasons.append(f"多链合计成交 {fmt_usd(total)} ≥ {fmt_usd(volume_usd_min)}")

    if dod is not None and abs(float(dod)) >= dod_pct_min:
        reasons.append(f"多链合计日环比 {float(dod):+.1f}%（线 {dod_pct_min:g}%）")

    best_chain = None
    best_abs = 0.0
    for c in chains:
        cd = c.get("dod_pct")
        if cd is None:
            continue
        if abs(float(cd)) >= chain_dod_pct_min and abs(float(cd)) >= best_abs:
            best_abs = abs(float(cd))
            best_chain = c
    if best_chain is not None:
        lab = best_chain.get("label") or CHAIN_LABEL.get(best_chain.get("chain", ""), "?")
        reasons.append(
            f"{lab} 日环比 {float(best_chain['dod_pct']):+.1f}%（单链线 {chain_dod_pct_min:g}%）"
        )

    hit = bool(reasons)
    if hit:
        tip = "；".join(reasons)
    elif dod is None and volume_usd_min <= 0:
        tip = "尚无日环比（需至少两天快照），且未开启成交额阈值 → 不推阈值"
    else:
        tip = (
            f"未过线：合计环比={dod if dod is not None else 'n/a'} "
            f"(需|{dod_pct_min:g}%|)；单链线 {chain_dod_pct_min:g}%"
            + (f"；成交额线 {fmt_usd(volume_usd_min)}" if volume_usd_min > 0 else "")
        )
    return {"hit": hit, "reasons": reasons, "tip": tip}


def fmt_chg_zh(pct: float) -> str:
    """新闻体：增长 / 下降 x.x%（不用 +/-）。"""
    if pct >= 0:
        return f"增长 {pct:.1f}%"
    return f"下降 {abs(pct):.1f}%"


def build_body(snap: dict[str, Any]) -> str:
    """吴说新闻体（与示例稿同结构），不含抬头标记。"""
    total = float(snap["total_volume"])
    chains = list(snap.get("chains") or [])
    stamp = snap.get("fetched_at_cn_text") or snap.get("day")
    dod = snap.get("dod_pct")

    by_id = {str(c.get("chain")): c for c in chains if isinstance(c, dict)}
    ordered: list[dict[str, Any]] = []
    for cid in CHAIN_ORDER:
        if cid in by_id:
            ordered.append(by_id[cid])
    for c in chains:
        if c not in ordered:
            ordered.append(c)

    name_parts = [
        str(c.get("label") or CHAIN_LABEL.get(str(c.get("chain")), "?")) for c in ordered
    ]
    names = "、".join(name_parts)
    n = len(ordered)
    count_word = {2: "两者", 3: "三者", 4: "四者", 5: "五者"}.get(n, f"{n} 者")

    vol_parts = []
    share_parts = []
    for c in ordered:
        vol_parts.append(fmt_usd(float(c.get("volume") or 0)))
        share_parts.append(fmt_pct(float(c.get("share") or 0)))
    vols = "、".join(vol_parts)
    shares = "、".join(share_parts)

    tops = snap.get("top_tokens") or []
    top_names = "、".join(t.get("symbol") or "?" for t in tops[:3]) or "—"

    with_dod = [c for c in chains if c.get("dod_pct") is not None]
    highlight = None
    if with_dod:
        highlight = max(with_dod, key=lambda c: float(c["dod_pct"]))

    body = (
        f"据吴说数据中心统计，截至 {stamp}，"
        f"{names} 等主要 Meme 市场核心交易量约 {fmt_usd(total)}；"
        f"{count_word}成交量分别约 {vols}，占比 {shares}，前三项目为 {top_names}。"
    )
    if dod is not None:
        body += f"主要 Meme 市场交易量日环比{fmt_chg_zh(float(dod))}"
        if highlight is not None:
            lab = highlight.get("label") or CHAIN_LABEL.get(str(highlight.get("chain")), "?")
            hp = float(highlight["dod_pct"])
            rank = "增幅最高" if hp >= 0 else "表现最好"
            body += f"，其中 {lab} {rank}，日环比{fmt_chg_zh(hp)}"
        body += "。"
    body += "吴说提醒，Meme 币波动极大、风险极高，请勿盲从。"
    body += f"\n{DASHBOARD_URL}"
    return body


def with_mark(mark: str, text: str) -> str:
    if text.startswith(mark):
        return text
    return f"{mark}\n{text}"


def append_log(
    *,
    day: str,
    kind: str,
    ok: bool,
    tip: str,
    text: str,
) -> None:
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(
            f"=== {stamp} day={day} kind={kind} ok={ok} tip={tip}\n{text}\n\n"
        )


def push_one(
    *,
    webhook: str,
    secret: str | None,
    day: str,
    kind: str,
    text: str,
    tip: str,
    dry_run: bool,
) -> bool:
    print(f"\n----- {kind} -----")
    print(text)
    if dry_run:
        print(f"[dry-run] 未推送 kind={kind} day={day}", file=sys.stderr)
        return True
    try:
        result = send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
        print(f"[ERROR] Lark 发送失败 kind={kind}: {e}", file=sys.stderr)
        append_log(day=day, kind=kind, ok=False, tip=f"{tip} err={e}", text=text)
        return False

    ok = True
    if "code" in result:
        ok = str(result.get("code")) == "0"
    append_log(day=day, kind=kind, ok=ok, tip=f"{tip} resp={result}", text=text)
    if not ok:
        print(f"[ERROR] 推送失败 kind={kind}: {result}", file=sys.stderr)
        return False
    print(f"[pushed] kind={kind} 已发送到飞书。", file=sys.stderr)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="推送前先重新拉取 GMGN")
    parser.add_argument("--push", action="store_true", help="真正发到飞书群")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--force", action="store_true", help="忽略日去重（日常/阈值各自）")
    parser.add_argument(
        "--ignore-threshold",
        action="store_true",
        help="阈值一路强制当过线（仅联调；日常不受影响）",
    )
    parser.add_argument(
        "--daily-only",
        action="store_true",
        help="只处理【实时日常报告】",
    )
    parser.add_argument(
        "--threshold-only",
        action="store_true",
        help="只处理【历史阈值警报】",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV))
    args = parser.parse_args()

    if args.daily_only and args.threshold_only:
        print("[ERROR] --daily-only 与 --threshold-only 不能同时用", file=sys.stderr)
        return 1

    env = merge_env([Path(args.env_file), *ALT_ENVS])
    apply_env(env)

    dod_min = _env_float(env, "GMGN_DOD_PCT", DEFAULT_DOD_PCT)
    chain_min = _env_float(env, "GMGN_CHAIN_DOD_PCT", DEFAULT_CHAIN_DOD_PCT)
    vol_min = _env_float(env, "GMGN_VOLUME_USD", DEFAULT_VOLUME_USD)

    if args.refresh:
        import download as gmgn_download

        gmgn_download.load_env_file(Path(args.env_file))
        try:
            snap = gmgn_download.build_snapshot(
                limit=max(1, min(100, args.limit)), interval="24h"
            )
            snap = gmgn_download.attach_dod(snap)
            gmgn_download.write_snapshot(snap)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] refresh failed: {e}", file=sys.stderr)
            return 1
    else:
        if not JSON_PATH.is_file():
            print(
                f"[ERROR] 缺少 {JSON_PATH}，请先 --refresh 或运行 download.py",
                file=sys.stderr,
            )
            return 1
        snap = json.loads(JSON_PATH.read_text(encoding="utf-8"))

    gate = evaluate_threshold(
        snap,
        dod_pct_min=dod_min,
        chain_dod_pct_min=chain_min,
        volume_usd_min=vol_min,
    )
    if args.ignore_threshold and not gate["hit"]:
        gate = {
            "hit": True,
            "reasons": ["--ignore-threshold"],
            "tip": f"联调强制过线（原：{gate['tip']}）",
        }

    print(
        f"[threshold] hit={gate['hit']} | {gate['tip']} "
        f"| lines: dod≥{dod_min:g}% chain≥{chain_min:g}% vol≥{vol_min:g}",
        file=sys.stderr,
    )

    day = str(snap.get("day") or "")
    body = build_body(snap)
    do_daily = not args.threshold_only
    do_threshold = not args.daily_only

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    dry_run = not args.push

    if args.push and not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1

    ok_all = True
    sent_any = False

    # 1) 日常报告：每天一条
    if do_daily:
        daily_text = with_mark(LARK_MARK_DAILY, body)
        if not args.force and already_pushed_daily(day):
            print(f"[skip] 日常：{day} 今日已推送（可用 --force）", file=sys.stderr)
        else:
            if not push_one(
                webhook=webhook,
                secret=secret,
                day=day,
                kind="daily",
                text=daily_text,
                tip="日常报告",
                dry_run=dry_run,
            ):
                ok_all = False
            else:
                sent_any = True
            if args.push and do_threshold:
                time.sleep(SEND_GAP_SEC)

    # 2) 阈值警报：过线才另发
    if do_threshold:
        if not gate["hit"]:
            print(f"[skip] 阈值：未过线。{gate['tip']}", file=sys.stderr)
            if args.push:
                stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
                with LOG_PATH.open("a", encoding="utf-8") as f:
                    f.write(
                        f"=== {stamp} day={day} kind=threshold ok=False tip={gate['tip']}\n"
                    )
        elif not args.force and already_pushed_threshold(day):
            print(f"[skip] 阈值：{day} 今日已推送（可用 --force）", file=sys.stderr)
        else:
            thr_text = with_mark(LARK_MARK_THRESHOLD, body)
            if not push_one(
                webhook=webhook,
                secret=secret,
                day=day,
                kind="threshold",
                text=thr_text,
                tip=gate["tip"],
                dry_run=dry_run,
            ):
                ok_all = False
            else:
                sent_any = True

    if dry_run:
        print(
            f"\n[dry-run] day={day} daily={do_daily} threshold_hit={gate['hit']}",
            file=sys.stderr,
        )
        return 0

    if not ok_all:
        return 1
    if not sent_any:
        print("\n[done] 无新消息需发送。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
