from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run btc-index pipeline: Coinglass (+ optional Glassnode) download + csv_to_json"
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--recheck-days", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.6)
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--api-key", default=None, help="Coinglass API key")
    parser.add_argument(
        "--with-glassnode",
        action="store_true",
        help="Also run Glassnode download (API membership required)",
    )
    parser.add_argument("--glassnode-sleep", type=float, default=0.35)
    parser.add_argument("--glassnode-api-key", default=None)
    parser.add_argument("--glassnode-max-failures", type=int, default=6)
    parser.add_argument("--glassnode-cache-only", action="store_true", help="Glassnode: merge cache only, no API")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    if not args.skip_download:
        cg_cmd = [
            py,
            str(BASE_DIR / "download_coinglass.py"),
            "--recheck-days",
            str(args.recheck_days),
            "--sleep",
            str(args.sleep),
            "--retry",
            str(args.retry),
        ]
        if args.force:
            cg_cmd.append("--force")
        if args.api_key:
            cg_cmd.extend(["--api-key", args.api_key])
        run_step(cg_cmd, "Coinglass: download + merge CSV")

        if args.with_glassnode or args.glassnode_cache_only:
            gn_cmd = [py, str(BASE_DIR / "download_glassnode.py"), "--recheck-days", str(args.recheck_days)]
            if args.force:
                gn_cmd.append("--force")
            gn_cmd.extend(["--sleep", str(args.glassnode_sleep), "--max-failures", str(args.glassnode_max_failures)])
            if args.glassnode_api_key:
                gn_cmd.extend(["--api-key", args.glassnode_api_key])
            if args.glassnode_cache_only:
                gn_cmd.append("--cache-only")
            run_step(gn_cmd, "Glassnode: download + merge CSV")
        else:
            print("\n[skip] Glassnode download (use --with-glassnode when API is active)", flush=True)

    if not args.skip_convert:
        convert_cmd = [py, str(BASE_DIR / "csv_to_json.py")]
        if not (BASE_DIR / "output" / "csv" / "glassnode" / "indicators.csv").exists():
            convert_cmd.append("--skip-glassnode")
        run_step(convert_cmd, "CSV -> JSON (coinglass + glassnode)")

    # MVRV / SSR 不再走 Glassnode。SSR 的分母是 stablecoin 管道的 JSON；
    # 日更里 misc 会在 stablecoin 刷新之后再跑一遍，这里保证单独跑 btc-index 也会落盘。
    run_step([py, str(BASE_DIR / "download_derived.py")], "Derived: MVRV Z-Score + SSR")

    print("\nDONE. outputs:", flush=True)
    print("  output/csv/coinglass/", flush=True)
    print("  output/csv/glassnode/", flush=True)
    print("  output/json/coinglass/  (incl. btc_spot_daily.json)", flush=True)
    print("  output/json/glassnode/", flush=True)


if __name__ == "__main__":
    main()
