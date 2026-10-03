"""清理已废弃的日线/基差历史产物（live-only 模式）。"""
from __future__ import annotations

import shutil
from pathlib import Path

import _paths as P


def _purge_dir(path: Path) -> int:
    if not path.is_dir():
        return 0
    n = sum(1 for p in path.rglob("*") if p.is_file())
    shutil.rmtree(path)
    return n


def purge_legacy_history(*, dashboard_json: Path | None = None) -> dict[str, int]:
    """删除 tradfi-spot-price / tradfi-arb-spread 本地与 dashboard 镜像。"""
    out: dict[str, int] = {}
    for label, path in (
        ("local_spot_price", P.SPOT_PRICE_DIR),
        ("local_arb_spread", P.ARB_SPREAD_DIR),
    ):
        out[label] = _purge_dir(path)

    if dashboard_json is not None:
        for label, name in (
            ("dash_spot_price", "tradfi-spot-price"),
            ("dash_arb_spread", "tradfi-arb-spread"),
        ):
            out[label] = _purge_dir(dashboard_json / name)
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dashboard",
        default="/root/dashboard/public/json",
        help="同时清理 dashboard 镜像目录",
    )
    ap.add_argument("--no-dashboard", action="store_true")
    args = ap.parse_args()
    dash = None if args.no_dashboard else Path(args.dashboard)
    stats = purge_legacy_history(dashboard_json=dash)
    for k, v in stats.items():
        print(f"{k}: removed {v} files", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
