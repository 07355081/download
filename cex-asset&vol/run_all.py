from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _paths import ensure_cache_layout

BASE_DIR = Path(__file__).resolve().parent
STEP_1 = BASE_DIR / "1.coingecko-vol.py"
STEP_2 = BASE_DIR / "2.btc_hourly_data.py"
STEP_3 = BASE_DIR / "3.fetch_cex_total_assets_merged.py"
STEP_4 = BASE_DIR / "4.adjust_cex_volume_by_assets.py"
STEP_CONVERT = BASE_DIR / "csv_to_json.py"

def run_step(cmd: list[str], title: str, required: bool = True) -> bool:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    proc = subprocess.run(cmd)
    if proc.returncode == 0:
        return True
    if required:
        raise SystemExit(f"[FATAL] {title} failed (exit {proc.returncode})")
    # 下载步骤各自独立取数，一个源失败不应让后续步骤全部不执行。
    # 曾因 step 1 对 null 成交量抛 TypeError，连带 step 2-5 数月未跑。
    print(f"[WARN] {title} failed (exit {proc.returncode}); continuing", flush=True)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run cex-asset&vol pipeline: download → adjust → csv_to_json",
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocesses")
    parser.add_argument("--k", type=float, default=2.0, help="Pass-through --k for step 4")
    parser.add_argument(
        "--vol-mode",
        choices=("append", "update"),
        default="append",
        help="Pass-through to step 1: append=补洞+tip（生产默认）; "
        "update=危险覆盖近一年（需 --i-know-this-rewrites-history）",
    )
    parser.add_argument(
        "--i-know-this-rewrites-history",
        action="store_true",
        help="Forwarded to step 1 when --vol-mode update",
    )
    parser.add_argument("--skip-step1", action="store_true")
    parser.add_argument("--skip-step2", action="store_true")
    parser.add_argument("--skip-step3", action="store_true")
    parser.add_argument("--skip-step4", action="store_true")
    parser.add_argument("--skip-convert", action="store_true", help="Skip csv_to_json.py")
    return parser.parse_args()


def main() -> None:
    notes = ensure_cache_layout()
    for line in notes:
        print(f"[layout] {line}", flush=True)

    args = parse_args()
    py = args.python

    started = datetime.now(timezone.utc)
    failed: list[str] = []
    print(f"pipeline start (UTC): {started.isoformat()}", flush=True)

    if not args.skip_step1:
        step1_cmd = [py, str(STEP_1), "--mode", args.vol_mode]
        if args.i_know_this_rewrites_history:
            step1_cmd.append("--i-know-this-rewrites-history")
        if not run_step(step1_cmd, "Step 1/5: download CEX volume", required=False):
            failed.append("step 1 (CEX volume)")
    if not args.skip_step2:
        if not run_step([py, str(STEP_2)], "Step 2/5: download BTC hourly and build daily VWAP", required=False):
            failed.append("step 2 (BTC hourly)")
    if not args.skip_step3:
        if not run_step([py, str(STEP_3)], "Step 3/5: download CEX total assets", required=False):
            failed.append("step 3 (CEX assets)")
    if not args.skip_step4:
        run_step([py, str(STEP_4), "--k", str(args.k)], "Step 4/5: adjust CEX volume by assets")
    if not args.skip_convert:
        run_step([py, str(STEP_CONVERT)], "Step 5/5: convert CSV/XLSX to dashboard JSON")

    ended = datetime.now(timezone.utc)
    print("pipeline artifacts: CSV/XLSX from steps 1-4, JSON under output/json/", flush=True)
    print(f"pipeline done (UTC): {ended.isoformat()} | elapsed={ended - started}", flush=True)
    if failed:
        # 非零退出码让 cron 日志与新鲜度告警能看见部分失败。
        print(f"[WARN] {len(failed)} download step(s) failed: {', '.join(failed)}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

