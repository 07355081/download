from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_1 = BASE_DIR / "download.py"
STEP_2 = BASE_DIR / "csv_to_json.py"


def today_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run options DVOL pipeline: download + csv_to_json")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocesses")
    parser.add_argument("--skip-download", action="store_true", help="Skip download.py")
    parser.add_argument("--skip-convert", action="store_true", help="Skip csv_to_json.py")
    parser.add_argument("--symbol", default=None, help="Pass-through symbol filter, e.g. BTC/ETH")
    parser.add_argument("--start", default="20210323", help="Pass-through start date for download.py")
    parser.add_argument("--end", default=None, help="Pass-through end date for download.py (default: today UTC)")
    parser.add_argument("--resolution", default="3600", help="Pass-through resolution for download.py")
    parser.add_argument("--retry", type=int, default=4, help="Pass-through retry for download.py")
    parser.add_argument("--sleep", type=float, default=0.2, help="Pass-through sleep for download.py")
    parser.add_argument("--testnet", action="store_true", help="Pass-through testnet for download.py")
    parser.add_argument("--compact", action="store_true", help="Pass-through compact for csv_to_json.py")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python
    end = args.end or today_yyyymmdd()

    if not args.skip_download:
        cmd = [
            py,
            str(STEP_1),
            "--start",
            str(args.start),
            "--end",
            str(end),
            "--resolution",
            str(args.resolution),
            "--retry",
            str(args.retry),
            "--sleep",
            str(args.sleep),
        ]
        if args.symbol:
            cmd.extend(["--symbol", args.symbol])
        if args.testnet:
            cmd.append("--testnet")
        run_step(cmd, "Step 1/2: download options DVOL CSV")

    if not args.skip_convert:
        cmd = [py, str(STEP_2)]
        if args.symbol:
            cmd.extend(["--symbol", args.symbol])
        if args.compact:
            cmd.append("--compact")
        run_step(cmd, "Step 2/2: convert DVOL CSV to JSON")

    print("\nDONE. outputs under options/output/", flush=True)


if __name__ == "__main__":
    main()
