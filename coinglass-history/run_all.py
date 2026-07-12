"""Run each subfolder's download.py and csv_to_json.py (no root-level scripts).

Only invokes:
  <module>/download.py
  <module>/csv_to_json.py   (if present)

Does NOT run copy_to_dashboard.py, _coin_pairs.py, etc.

Usage:
    python run_all.py
    python run_all.py --skip-download
    python run_all.py --only etf-list,etf-flow-history
    python run_all.py --skip-heavy --max-tuples 10
    python run_all.py --workers 4 --sleep 0.25
    python run_all.py --fail-fast   # abort on first module failure (old default)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Folder naming: /api/<segments> → <segments joined by '-'>
# e.g. /api/futures/open-interest/history → futures-open-interest-history

# Subfolder run order (deps first, heavy jobs last). Unknown dirs run after these.
MODULE_ORDER: tuple[str, ...] = (
    "etf-list",
    "etf-aum",
    "etf-flow-history",
    "etf-net-assets-history",
    "etf-premium-discount-history",
    "etf-history",
    "option-max-pain-history",
    "option-exchange-oi-history",
    "option-exchange-vol-history",
    "futures-open-interest-aggregated-stablecoin-history",
    "futures-open-interest-aggregated-coin-margin-history",
    "futures-funding-rate-oi-weight-history",
    "futures-funding-rate-vol-weight-history",
    "futures-open-interest-history",
    "futures-price-history",
    "spot-price-history",
)

HEAVY_MODULES = frozenset({
    "futures-open-interest-history",
    "futures-price-history",
    "spot-price-history",
})

ETF_MODULES = frozenset({
    "etf-aum",
    "etf-flow-history",
    "etf-net-assets-history",
    "etf-premium-discount-history",
    "etf-history",
})

CONVERT_FILTER_MODULES = frozenset({
    "futures-open-interest-aggregated-stablecoin-history",
    "futures-open-interest-aggregated-coin-margin-history",
    "futures-funding-rate-oi-weight-history",
    "futures-funding-rate-vol-weight-history",
    "futures-open-interest-history",
    "futures-price-history",
    "spot-price-history",
})


def discover_modules() -> list[dict[str, object]]:
    """Only subdirectories that contain download.py."""
    by_name: dict[str, dict[str, object]] = {}
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name in ("symbols", "__pycache__"):
            continue
        if not (d / "download.py").is_file():
            continue
        name = d.name
        by_name[name] = {
            "name": name,
            "dir": d,
            "convert": (d / "csv_to_json.py").is_file(),
            "etf": name in ETF_MODULES,
            "heavy": name in HEAVY_MODULES,
        }
    ordered: list[dict[str, object]] = []
    for name in MODULE_ORDER:
        if name in by_name:
            ordered.append(by_name.pop(name))
    for name in sorted(by_name):
        ordered.append(by_name[name])
    return ordered


def run_step(cmd: list[str], title: str, *, check: bool) -> int:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return r.returncode


def _append_opt(cmd: list[str], flag: str, value: object | None) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        if value:
            cmd.append(flag)
        return
    cmd.extend([flag, str(value)])


def build_download_cmd(py: str, meta: dict, args: argparse.Namespace) -> list[str]:
    """Only pass CLI flags each subfolder's download.py actually defines."""
    mod_dir: Path = meta["dir"]  # type: ignore[assignment]
    name = str(meta["name"])
    cmd = [py, str(mod_dir / "download.py")]

    if name == "etf-list":
        _append_opt(cmd, "--market", args.market)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name == "etf-aum":
        _append_opt(cmd, "--market", args.market)
        _append_opt(cmd, "--force", args.force)
        _append_opt(cmd, "--recheck-days", args.recheck_days)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name in ("etf-flow-history", "etf-net-assets-history"):
        _append_opt(cmd, "--market", args.market)
        _append_opt(cmd, "--force", args.force)
        _append_opt(cmd, "--recheck-days", args.recheck_days)
        _append_opt(cmd, "--retry", args.retry)
        _append_opt(cmd, "--sleep", args.sleep)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name == "etf-premium-discount-history":
        _append_opt(cmd, "--force", args.force)
        _append_opt(cmd, "--recheck-days", args.recheck_days)
        _append_opt(cmd, "--retry", args.retry)
        _append_opt(cmd, "--sleep", args.sleep)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name == "etf-history":
        _append_opt(cmd, "--market", args.market)
        _append_opt(cmd, "--ticker", args.ticker)
        _append_opt(cmd, "--force", args.force)
        _append_opt(cmd, "--recheck-days", args.recheck_days)
        _append_opt(cmd, "--retry", args.retry)
        _append_opt(cmd, "--sleep", args.sleep)
        _append_opt(cmd, "--max-failures", args.max_failures)
        _append_opt(cmd, "--max-tuples", args.max_tuples if args.max_tuples > 0 else None)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name == "option-max-pain-history":
        _append_opt(cmd, "--symbol", args.symbol)
        _append_opt(cmd, "--exchange", args.exchange)
        _append_opt(cmd, "--retry", args.retry)
        _append_opt(cmd, "--sleep", args.sleep)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    if name in ("option-exchange-oi-history", "option-exchange-vol-history"):
        _append_opt(cmd, "--symbol", args.symbol)
        _append_opt(cmd, "--force", args.force)
        _append_opt(cmd, "--retry", args.retry)
        _append_opt(cmd, "--sleep", args.sleep)
        _append_opt(cmd, "--workers", args.workers)
        _append_opt(cmd, "--api-key", args.api_key)
        return cmd

    # _runner per-instrument / per-symbol modules
    _append_opt(cmd, "--force", args.force)
    _append_opt(cmd, "--retry", args.retry)
    _append_opt(cmd, "--sleep", args.sleep)
    _append_opt(cmd, "--workers", args.workers)
    _append_opt(cmd, "--recheck-days", args.recheck_days)
    _append_opt(cmd, "--max-failures", args.max_failures)
    _append_opt(cmd, "--max-tuples", args.max_tuples if args.max_tuples > 0 else None)
    _append_opt(cmd, "--symbol", args.symbol)
    _append_opt(cmd, "--interval", args.interval)
    _append_opt(cmd, "--exchange", args.exchange)
    _append_opt(cmd, "--api-key", args.api_key)
    return cmd


def build_convert_cmd(py: str, meta: dict, args: argparse.Namespace) -> list[str]:
    mod_dir: Path = meta["dir"]  # type: ignore[assignment]
    name = str(meta["name"])
    cmd = [py, str(mod_dir / "csv_to_json.py")]
    if meta.get("etf") or name in ("etf-history", "etf-list"):
        _append_opt(cmd, "--market", args.market)
    if name == "option-max-pain-history":
        _append_opt(cmd, "--symbol", args.symbol)
        _append_opt(cmd, "--exchange", args.exchange)
    if name == "etf-history":
        _append_opt(cmd, "--ticker", args.ticker)
    if name in CONVERT_FILTER_MODULES:
        _append_opt(cmd, "--symbol", args.symbol)
        _append_opt(cmd, "--interval", args.interval)
    return cmd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run subfolder download.py + csv_to_json.py only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--skip-convert", action="store_true")
    p.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop pipeline on first module failure (default: continue and report at end)",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--only", default=None, help="Comma-separated subfolder names")
    p.add_argument("--skip", default=None, help="Comma-separated subfolder names to skip")
    p.add_argument("--skip-heavy", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--api-key", default=None)
    p.add_argument("--recheck-days", type=int, default=2)
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--sleep", type=float, default=0.25)
    p.add_argument("--workers", type=int, default=4, help="Concurrent API workers per module (default 4)")
    p.add_argument("--max-failures", type=int, default=50)
    p.add_argument("--max-tuples", type=int, default=0)
    p.add_argument("--symbol", default=None)
    p.add_argument("--interval", default=None)
    p.add_argument("--exchange", default=None)
    p.add_argument("--market", default=None)
    p.add_argument("--ticker", default=None)
    return p.parse_args()


def selected_modules(all_mods: list[dict], args: argparse.Namespace) -> list[dict]:
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    skip = set(s.strip() for s in args.skip.split(",")) if args.skip else set()
    out: list[dict] = []
    for meta in all_mods:
        name = str(meta["name"])
        if only is not None and name not in only:
            continue
        if name in skip:
            continue
        if args.skip_heavy and meta.get("heavy"):
            continue
        out.append(meta)
    return out


def main() -> None:
    args = parse_args()
    py = args.python
    mods = selected_modules(discover_modules(), args)
    if not mods:
        sys.exit("No modules selected (no subfolder with download.py, or --only/--skip filtered all).")

    started = datetime.now(timezone.utc)
    print(f"coinglass-history pipeline start (UTC): {started.isoformat()}", flush=True)
    print(f"modules: {len(mods)}", flush=True)

    failures: list[str] = []
    fail_fast = args.fail_fast and not args.continue_on_error
    total = len(mods)

    for i, meta in enumerate(mods, 1):
        name = str(meta["name"])
        mod_dir: Path = meta["dir"]  # type: ignore[assignment]

        if not args.skip_download:
            rc = run_step(
                build_download_cmd(py, meta, args),
                f"[{i}/{total}] {name} — download",
                check=False,
            )
            if rc != 0:
                failures.append(f"{name}/download (exit {rc})")
                print(
                    f"\n[WARN] {name}/download 退出码 {rc}，"
                    f"已成功写入的 cache 保留；继续后续步骤。",
                    flush=True,
                )
                if fail_fast:
                    print(
                        f"[ABORT] --fail-fast：后续模块未执行。",
                        flush=True,
                    )
                    sys.exit(rc)

        if meta.get("convert") and not args.skip_convert:
            rc = run_step(
                build_convert_cmd(py, meta, args),
                f"[{i}/{total}] {name} — csv_to_json",
                check=False,
            )
            if rc != 0:
                failures.append(f"{name}/csv_to_json (exit {rc})")
                print(
                    f"\n[WARN] {name}/csv_to_json 退出码 {rc}，继续下一模块。",
                    flush=True,
                )
                if fail_fast:
                    print(
                        f"[ABORT] --fail-fast：后续模块未执行。",
                        flush=True,
                    )
                    sys.exit(rc)

    ended = datetime.now(timezone.utc)
    print(f"\npipeline done (UTC): {ended.isoformat()} | elapsed={ended - started}", flush=True)
    if failures:
        print(f"failures ({len(failures)}): {', '.join(failures)}", flush=True)
        sys.exit(2)
    print("all steps OK", flush=True)


if __name__ == "__main__":
    main()
