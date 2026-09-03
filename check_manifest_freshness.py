#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 _manifest.json 各数据集的 last_day，超阈值推飞书。

在 gen_dashboard_meta.py 之后运行。历史上多个数据集静默停更而无人发现
（cex-asset-vol 停 10 天、crypto-treasuries 停 45 天、etf-premium-discount
停 105 天），本脚本补上缺失的那道闸门。

已知长期停更项在 THRESHOLDS / EXEMPT 里单列：告警一旦天天刷屏就会被当成
噪音忽略，等于没接。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_MANIFEST = Path("/root/dashboard/public/json/_manifest.json")
DEFAULT_ENV_FILE = Path("/root/dashboard/.env.live")
HTTP_TIMEOUT = 10

# 日更数据集的容忍天数。数据本身通常是"昨天"的，所以 2 天已含一天余量。
DEFAULT_MAX_LAG_DAYS = 2

# 只在交易日更新的数据集：周末与假日会自然滞后，4 天可覆盖周五到周一。
TRADING_DAY_LAG = 4

THRESHOLDS: dict[str, int] = {
    ".": TRADING_DAY_LAG,
    "etf-flow-history": TRADING_DAY_LAG,
    "etf-history": TRADING_DAY_LAG,
    # 已改为从 etf-history 现货 ticker 重建，跟美股交易日走。
    "etf-premium-discount-history": TRADING_DAY_LAG,
    "etf-net-assets-history": TRADING_DAY_LAG,
    "strategy-mstr": TRADING_DAY_LAG,
    "tradfi-funding": TRADING_DAY_LAG,
    "tradfi-oi": TRADING_DAY_LAG,
    "tradfi-price": TRADING_DAY_LAG,
    # crypto-treasuries 走每周日 09:00 的全量刷新（run_treasuries_weekly.sh），
    # 正常节奏下最大滞后 7 天，10 天可容纳周更又能在连续两周失败时告警。
    "crypto-treasuries": 10,
    # 本机人工推送：RootData 的人机检测绑定出口 IP，机房 IP 过不了。
    "financing-news": 14,
    # 本机生成 + 人工纠错后定期上传，季度级节奏。
    "tag": 120,
}

# 豁免：上游已冻结，或文件本身不含可识别日期字段。纳入告警只会制造噪音。
EXEMPT: dict[str, str] = {
    "etf-aum": "CoinGlass 上游冻结，待核对端点状态",
    "website-traffic": "SimilarWeb 月度，本机手动上传",
    "etf-list": "清单文件，无日期字段",
    "option-max-pain-history": "gen_dashboard_meta 未能解析其日期字段",
}


def load_env(path: Path) -> dict[str, str]:
    """读取 .env 风格文件。只取本脚本需要的键，不污染 os.environ。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def gen_lark_sign(secret: str, timestamp: str) -> str:
    """飞书自定义机器人签名：HMAC-SHA256(key=f"{ts}\\n{secret}", msg=""), Base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, object] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return  # 部分机器人成功时返回空体
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        msg = parsed.get("msg") or parsed.get("StatusMessage") or body[:200]
        raise RuntimeError(f"Lark rejected: code={code} msg={msg}")


def parse_day(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="按阈值检查 manifest 数据新鲜度并推飞书")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    p.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    p.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="有超阈值项时以退出码 1 结束（默认 0，因为数据滞后是业务状态而非脚本失败）",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.manifest.is_file():
        raise SystemExit(f"manifest not found: {args.manifest}")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    datasets = manifest.get("datasets") or {}
    if not datasets:
        raise SystemExit("manifest has no datasets")

    today = date.today()
    stale: list[tuple[str, date, int, int]] = []
    missing: list[str] = []
    exempted: list[str] = []
    ok_count = 0

    for name in sorted(datasets):
        if name in EXEMPT:
            exempted.append(name)
            continue
        limit = THRESHOLDS.get(name, DEFAULT_MAX_LAG_DAYS)
        last_day = parse_day(datasets[name].get("last_day"))
        if last_day is None:
            missing.append(name)
            continue
        lag = (today - last_day).days
        if lag > limit:
            stale.append((name, last_day, lag, limit))
        else:
            ok_count += 1

    print(f"manifest generated_at: {manifest.get('generated_at')}")
    print(f"today: {today} | datasets: {len(datasets)}")
    print(f"ok={ok_count} stale={len(stale)} no_date={len(missing)} exempt={len(exempted)}")

    if stale:
        print("\n-- STALE --")
        for name, last_day, lag, limit in stale:
            print(f"  {name:32s} last={last_day} lag={lag}d limit={limit}d")
    if missing:
        print("\n-- NO DATE (未豁免却解析不出日期，需检查) --")
        for name in missing:
            print(f"  {name}")
    if exempted:
        print("\n-- EXEMPT --")
        for name in exempted:
            print(f"  {name:32s} {EXEMPT[name]}")

    alarms = stale + [(n, None, None, None) for n in missing]  # type: ignore[list-item]
    if not alarms:
        print("\nAll datasets within threshold. No alert sent.")
        return

    lines = [f"【数据新鲜度告警】{len(stale) + len(missing)} 项超阈值 / 无日期"]
    for name, last_day, lag, limit in stale:
        lines.append(f"· {name} 滞后 {lag} 天（{last_day}，阈值 {limit} 天）")
    for name in missing:
        lines.append(f"· {name} 解析不出日期")
    gen = manifest.get("generated_at") or "?"
    lines.append(f"manifest: {gen}")
    lines.append("https://data.wublock123.com/dashboard")
    text = "\n".join(lines)

    print("\n-- ALERT TEXT --")
    print(text)

    if args.dry_run:
        print("\n[dry-run] not sending")
    else:
        env = load_env(args.env_file)
        webhook = env.get("LARK_ALERT_WEBHOOK", "").strip()
        secret = env.get("LARK_ALERT_SECRET", "").strip() or None
        if not webhook:
            print(f"\n[WARN] LARK_ALERT_WEBHOOK missing in {args.env_file}; skip sending")
        else:
            try:
                send_lark_text(webhook, secret, text)
                print("\n[sent] Lark alert delivered")
            except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
                print(f"\n[WARN] Lark send failed: {e}")

    if args.fail_on_stale:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
