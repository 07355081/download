from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_REALTIME = BASE_DIR / "1.download_realtime.py"
STEP_HASHRATE = BASE_DIR / "2.download_hashrate.py"
STEP_SHUTDOWN = BASE_DIR / "3.compute_shutdown.py"
STEP_CONVERT = BASE_DIR / "4.csv_to_json.py"


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run mining shutdown pipeline: 1.download_realtime → 2.download_hashrate → 3.compute_shutdown → 4.csv_to_json"
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--skip-realtime", action="store_true", help="Skip 1.download_realtime.py")
    parser.add_argument("--skip-hashrate", action="store_true", help="Skip 2.download_hashrate.py")
    parser.add_argument("--skip-shutdown", action="store_true", help="Skip 3.compute_shutdown.py")
    parser.add_argument("--skip-convert", action="store_true", help="Skip 4.csv_to_json.py")
    parser.add_argument("--skip-bitinfocharts", action="store_true")
    parser.add_argument("--skip-2miners", action="store_true")
    parser.add_argument("--coins", default="bch,etc,kas,ckb,zec")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--realtime-coins",
        default="BTC,BCH,BSV,ALPH,CKB,DASH,ETC,HNS,KAS,KDA,LTC+DOGE+BELLS+JKC+LKY+PEP,XMR,ZEC",
    )
    parser.add_argument("--realtime-electricity-cents", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    if not args.skip_realtime:
        cmd = [
            py,
            str(STEP_REALTIME),
            "--coins",
            str(args.realtime_coins),
            "--electricity-cents",
            str(args.realtime_electricity_cents),
            "--out",
            str(BASE_DIR / "output" / "csv" / "realtime_mining_shutdown_price_snapshot.csv"),
        ]
        run_step(cmd, "Step 1/4: download realtime shutdown snapshot")

    if not args.skip_hashrate:
        cmd = [py, str(STEP_HASHRATE), "--coins", str(args.coins)]
        if args.skip_bitinfocharts:
            cmd.append("--skip-bitinfocharts")
        if args.skip_2miners:
            cmd.append("--skip-2miners")
        run_step(cmd, "Step 2/4: download hashrate + BTC chain metrics")

    if not args.skip_shutdown:
        run_step([py, str(STEP_SHUTDOWN)], "Step 3/4: compute BTC shutdown price history CSV")

    if not args.skip_convert:
        cmd = [
            py,
            str(STEP_CONVERT),
            "--history-in",
            str(BASE_DIR / "output" / "csv" / "shutdown-price-all.csv"),
            "--history-out",
            str(BASE_DIR / "output" / "json" / "mining_shutdown_price.json"),
            "--realtime-in",
            str(BASE_DIR / "output" / "csv" / "realtime_mining_shutdown_price_snapshot.csv"),
            "--realtime-out",
            str(BASE_DIR / "output" / "json" / "realtime_mining_shutdown_price_snapshot.json"),
        ]
        if args.compact:
            cmd.append("--compact")
        run_step(cmd, "Step 4/4: convert CSV to dashboard JSON")

    print("\nDONE. outputs under mining-shutdown-price/output/", flush=True)


if __name__ == "__main__":
    main()
