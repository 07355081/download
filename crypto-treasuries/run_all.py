"""Run crypto-treasuries pipeline: 1.download → 2.mark_holdings → 3.csv_to_json.

Usage:
    python run_all.py
    python run_all.py --skip-download
    python run_all.py --skip-mark
    python run_all.py --coin bitcoin,ethereum --days 365,180,90,30
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_DOWNLOAD = BASE_DIR / "1.download.py"
STEP_MARK = BASE_DIR / "2.mark_holdings.py"
STEP_CONVERT = BASE_DIR / "3.csv_to_json.py"


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run crypto-treasuries pipeline: CoinGecko download + cache to JSON"
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocesses")
    parser.add_argument("--skip-download", action="store_true", help="Skip 1.download.py")
    parser.add_argument("--skip-mark", action="store_true", help="Skip 2.mark_holdings.py")
    parser.add_argument("--skip-convert", action="store_true", help="Skip 3.csv_to_json.py")

    parser.add_argument(
        "--coin",
        default=None,
        help="Pass-through coin filter (comma-separated), e.g. bitcoin,ethereum",
    )
    parser.add_argument(
        "--mode",
        choices=("web", "full"),
        default="full",
        help="Pass-through download mode (default: full)",
    )
    parser.add_argument(
        "--days",
        default="365,180,90,30",
        help="Pass-through chart day windows for download (dashboard fallback); also filters csv_to_json",
    )
    parser.add_argument("--enrich-count", type=int, default=8, help="Pass-through web mode enrich count")
    parser.add_argument("--enrich-batch-size", type=int, default=4, help="Pass-through detail batch size")
    parser.add_argument(
        "--enrich-batch-delay-ms",
        type=int,
        default=180,
        help="Pass-through delay between detail batches (ms)",
    )
    parser.add_argument("--coin-delay-s", type=float, default=1.0, help="Pass-through sleep between coins")
    parser.add_argument("--api-key", default=None, help="Pass-through CoinGecko API key")
    parser.add_argument("--retry", type=int, default=6, help="Pass-through retry count for 1.download.py")
    parser.add_argument("--sleep", type=float, default=1.5, help="Pass-through base sleep for 1.download.py")
    parser.add_argument("--force", action="store_true", help="Pass-through: ignore cache and re-fetch")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    if not args.skip_download:
        cmd = [
            py,
            str(STEP_DOWNLOAD),
            "--mode",
            args.mode,
            "--days",
            str(args.days),
            "--enrich-count",
            str(args.enrich_count),
            "--enrich-batch-size",
            str(args.enrich_batch_size),
            "--enrich-batch-delay-ms",
            str(args.enrich_batch_delay_ms),
            "--coin-delay-s",
            str(args.coin_delay_s),
            "--retry",
            str(args.retry),
            "--sleep",
            str(args.sleep),
        ]
        if args.coin:
            cmd.extend(["--coin", args.coin])
        if args.api_key:
            cmd.extend(["--api-key", args.api_key])
        if args.force:
            cmd.append("--force")
        run_step(cmd, "Step 1/3: download CoinGecko treasuries to cache")

    if not args.skip_mark:
        cmd = [py, str(STEP_MARK)]
        if args.coin:
            cmd.extend(["--coin", args.coin])
        run_step(cmd, "Step 2/3: mark skip_json=1 in holdings.csv")

    if not args.skip_convert:
        cmd = [py, str(STEP_CONVERT)]
        if args.coin:
            cmd.extend(["--coin", args.coin])
        if args.days:
            cmd.extend(["--days", str(args.days)])
        run_step(cmd, "Step 3/3: convert cache to dashboard JSON")

    print("\nDONE. outputs under crypto-treasuries/", flush=True)
    print("  output/holdings.csv", flush=True)
    print("  output/json/", flush=True)
    print("  cache/ (API cache + download_summary.csv)", flush=True)


if __name__ == "__main__":
    main()
