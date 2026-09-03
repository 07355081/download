"""Run every subfolder run_all.py in data-download (root orchestrator).

Modules (8):
  coinglass-history, btc-index, cex-asset&vol, crypto-treasuries,
  mining-shutdown-price, options, stablecoin, financing-news

Usage:
    python run_all.py
    python run_all.py --only coinglass-history,btc-index
    python run_all.py --skip coinglass-history
    python run_all.py --with-dashboard
    python run_all.py --only coinglass-history -- --skip spot-price-history,futures-price-history
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MODULE_ORDER: tuple[str, ...] = (
    "coinglass-history",
    "btc-index",
    "cex-asset&vol",
    "crypto-treasuries",
    "mining-shutdown-price",
    "options",
    "stablecoin",
    "financing-news",
)


def discover_modules() -> list[str]:
    found = {d.name for d in ROOT.iterdir() if d.is_dir() and (d / "run_all.py").is_file()}
    ordered = [name for name in MODULE_ORDER if name in found]
    for name in sorted(found - set(ordered)):
        ordered.append(name)
    return ordered


def run_step(cmd: list[str], title: str) -> int:
    print(f"\n{'=' * 72}", flush=True)
    print(f"=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def print_failure_summary(failures: list[str]) -> None:
    print(f"\n{'=' * 72}", flush=True)
    print(f"=== 错误汇总 ({len(failures)} 项) ===", flush=True)
    for i, msg in enumerate(failures, 1):
        print(f"  [{i}] {msg}", flush=True)


def normalize_pass_through_args(extra: list[str]) -> list[str]:
    """Drop the POSIX-style ``--`` separator before forwarding to submodules."""
    if extra and extra[0] == "--":
        return extra[1:]
    return extra


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(
        description="Run each subfolder run_all.py under data-download",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--only", default=None, help="Comma-separated module folder names")
    p.add_argument("--skip", default=None, help="Comma-separated module folder names to skip")
    p.add_argument(
        "--with-dashboard",
        action="store_true",
        help="After all modules, run copy_to_dashboard.py",
    )
    p.add_argument(
        "--dry-run-dashboard",
        action="store_true",
        help="With --with-dashboard, pass --dry-run to copy_to_dashboard.py",
    )
    args, extra = p.parse_known_args(argv)
    return args, normalize_pass_through_args(extra)


def selected_modules(all_mods: list[str], args: argparse.Namespace) -> list[str]:
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    skip = {s.strip() for s in args.skip.split(",") if s.strip()} if args.skip else set()
    out: list[str] = []
    for name in all_mods:
        if only is not None and name not in only:
            continue
        if name in skip:
            continue
        out.append(name)
    return out


def main(argv: list[str] | None = None) -> None:
    args, extra = parse_args(argv)
    py = args.python
    mods = selected_modules(discover_modules(), args)
    if not mods:
        sys.exit("No modules selected (no run_all.py found, or --only/--skip filtered all).")

    started = datetime.now(timezone.utc)
    print(f"data-download root pipeline start (UTC): {started.isoformat()}", flush=True)
    print(f"modules: {len(mods)} — {', '.join(mods)}", flush=True)
    if extra:
        print(f"pass-through args: {' '.join(extra)}", flush=True)

    failures: list[str] = []
    total = len(mods)

    for i, name in enumerate(mods, 1):
        cmd = [py, str(ROOT / name / "run_all.py"), *extra]
        try:
            rc = run_step(cmd, f"[{i}/{total}] {name} — run_all.py")
        except Exception as exc:
            failures.append(f"{name}/run_all.py (异常: {exc})")
            print(f"\n[WARN] {name}/run_all.py 异常: {exc}，跳过，继续下一模块。", flush=True)
            continue
        if rc != 0:
            failures.append(f"{name}/run_all.py (退出码 {rc})")
            print(f"\n[WARN] {name}/run_all.py 退出码 {rc}，跳过，继续下一模块。", flush=True)

    if args.with_dashboard:
        dash_cmd = [py, str(ROOT / "copy_to_dashboard.py")]
        if args.dry_run_dashboard:
            dash_cmd.append("--dry-run")
        label = "copy_to_dashboard.py"
        try:
            rc = run_step(dash_cmd, label)
        except Exception as exc:
            failures.append(f"{label} (异常: {exc})")
            print(f"\n[WARN] {label} 异常: {exc}，已记录。", flush=True)
        else:
            if rc != 0:
                failures.append(f"{label} (退出码 {rc})")
                print(f"\n[WARN] {label} 退出码 {rc}，已记录。", flush=True)

    ended = datetime.now(timezone.utc)
    print(f"\ndata-download root pipeline done (UTC): {ended.isoformat()} | elapsed={ended - started}", flush=True)
    if failures:
        print_failure_summary(failures)
        sys.exit(2)
    print("all modules OK", flush=True)


if __name__ == "__main__":
    main()
