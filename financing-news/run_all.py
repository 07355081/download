"""Run financing-news pipeline: incremental scrape + csv_to_json."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_SCRAPE = BASE_DIR / "scrape_rootdata.py"
STEP_CONVERT = BASE_DIR / "csv_to_json.py"


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run financing-news pipeline: RootData scrape + csv_to_json",
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocesses")
    parser.add_argument("--skip-download", action="store_true", help="Skip scrape_rootdata.py")
    parser.add_argument("--skip-convert", action="store_true", help="Skip csv_to_json.py")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Pass --full to scrape (disable early stop on duplicate pages)",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Pass-through --max-pages")
    parser.add_argument("--delay", type=int, default=None, help="Pass-through --delay")
    parser.add_argument("--compact", action="store_true", help="Pass-through compact for csv_to_json.py")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    if not args.skip_download:
        cmd = [py, str(STEP_SCRAPE)]
        # Default: incremental. Only pass --full when explicitly requested.
        if args.full:
            cmd.append("--full")
        if args.max_pages is not None:
            cmd.extend(["--max-pages", str(args.max_pages)])
        if args.delay is not None:
            cmd.extend(["--delay", str(args.delay)])
        run_step(cmd, "Step 1/2: scrape RootData (incremental by default)")

    if not args.skip_convert:
        cmd = [py, str(STEP_CONVERT)]
        if args.compact:
            cmd.append("--compact")
        run_step(cmd, "Step 2/2: convert fundraising CSV to JSON")

    print("\nDONE. outputs under financing-news/cache/ and financing-news/output/json/", flush=True)


if __name__ == "__main__":
    main()
