from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_1 = BASE_DIR / "1.coingecko-vol.py"
STEP_2 = BASE_DIR / "2.btc_hourly_data.py"
STEP_3 = BASE_DIR / "3.fetch_cex_total_assets_merged.py"
STEP_4 = BASE_DIR / "4.adjust_cex_volume_by_assets.py"
STEP_CONVERT = BASE_DIR / "csv_to_json.py"

def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


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
        help="Pass-through to step 1: append=仅补充未下载日期, update=更新最近365天",
    )
    parser.add_argument("--skip-step1", action="store_true")
    parser.add_argument("--skip-step2", action="store_true")
    parser.add_argument("--skip-step3", action="store_true")
    parser.add_argument("--skip-step4", action="store_true")
    parser.add_argument("--skip-convert", action="store_true", help="Skip csv_to_json.py")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    started = datetime.now(timezone.utc)
    print(f"pipeline start (UTC): {started.isoformat()}", flush=True)

    if not args.skip_step1:
        step1_cmd = [py, str(STEP_1)]
        step1_cmd += ["--mode", args.vol_mode]
        run_step(step1_cmd, "Step 1/5: download CEX volume")
    if not args.skip_step2:
        run_step([py, str(STEP_2)], "Step 2/5: download BTC hourly and build daily VWAP")
    if not args.skip_step3:
        run_step([py, str(STEP_3)], "Step 3/5: download CEX total assets")
    if not args.skip_step4:
        run_step([py, str(STEP_4), "--k", str(args.k)], "Step 4/5: adjust CEX volume by assets")
    if not args.skip_convert:
        run_step([py, str(STEP_CONVERT)], "Step 5/5: convert CSV/XLSX to dashboard JSON")

    ended = datetime.now(timezone.utc)
    print("pipeline artifacts: CSV/XLSX from steps 1-4, JSON under output/json/", flush=True)
    print(f"pipeline done (UTC): {ended.isoformat()} | elapsed={ended - started}", flush=True)


if __name__ == "__main__":
    main()

