r"""One-click copy of local JSON outputs to dashboard public/json/.

Layout:
  1) coinglass-history modules are mirrored 1:1 under public/json/<module>/
  2) crypto-treasuries flat files are copied to public/json/crypto-treasuries/
  3) cex-asset&vol flat files are copied to public/json/cex-asset-vol/
  4) tag flat files are copied to public/json/tag/
     (base_asset_categories_summary.json from summarize_base_asset_categories.py)
  5) btc-index coinglass/ + glassnode/ are copied to public/json/btc-index/ (same layout)
  6) options flat files are copied to public/json/option-iv/
  7) stablecoin flat files are copied to public/json/stablecoin/
  8) mining-shutdown-price flat files are copied to public/json/mining-shutdown-price/
  9) tradfi exchange-direct: tradfi/output/json/tradfi-price/*.json mirrored 1:1 to
     public/json/tradfi-price/ (isolated dir; safe mirror-delete here, never touches coinglass)

Sources:
  - <repo>/data-download/coinglass-history/<module>/output/json/*.json
  - <repo>/data-download/crypto-treasuries/output/json/*.json
  - <repo>/data-download/cex-asset&vol/output/json/*.json
  - <repo>/data-download/tag/output/json/base_asset_categories_summary.json
  - <repo>/data-download/btc-index/output/json/coinglass/*.json
  - <repo>/data-download/btc-index/output/json/glassnode/*.json
  - <repo>/data-download/options/output/json/*.json
  - <repo>/data-download/stablecoin/output/json/*.json
  - <repo>/data-download/mining-shutdown-price/output/json/*.json
Target:
  - <repo>/data-dashboard/public/json/<module>/*.json
  - (same subpaths under public/json/ as listed above)

Mirror sync:
    Copy every *.json from source into the matching dashboard folder, then remove
    dashboard JSON files that no longer exist in source (keeps download and dashboard aligned).
    Modules in COINGLASS_SYNC_EXCLUDE are skipped entirely (dashboard copies are not updated
    or deleted).

Copy rule:
    Skip copy if target exists AND size matches source (unless --force).

Use --force to overwrite all files regardless of size match.

Usage:
    python copy_to_dashboard.py
    python copy_to_dashboard.py --dry-run
    python copy_to_dashboard.py --force
    python copy_to_dashboard.py --module futures-price-history
    python copy_to_dashboard.py --only treasuries
    python copy_to_dashboard.py --only cex
    python copy_to_dashboard.py --only tag
    python copy_to_dashboard.py --only btc-index
    python copy_to_dashboard.py --only options
    python copy_to_dashboard.py --dest D:\custom\path
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COINGLASS_ROOT = SCRIPT_DIR / "coinglass-history"
TREASURIES_ROOT = SCRIPT_DIR / "crypto-treasuries"
CEX_ROOT = SCRIPT_DIR / "cex-asset&vol"
TAG_ROOT = SCRIPT_DIR / "tag"
BTC_INDEX_ROOT = SCRIPT_DIR / "btc-index"
OPTIONS_ROOT = SCRIPT_DIR / "options"
STABLECOIN_ROOT = SCRIPT_DIR / "stablecoin"
MINING_SHUTDOWN_PRICE_ROOT = SCRIPT_DIR / "mining-shutdown-price"
# TradFi 交易所直连输出（独立目录，与 coinglass 完全隔离）
TRADFI_ROOT = SCRIPT_DIR / "tradfi"

# 目标默认取同级 data-dashboard（跨机器稳健：download 与 dashboard 同父目录），
# 找不到再回退到旧的绝对路径。可用 --dest 覆盖。
def _default_dest() -> Path:
    sibling = SCRIPT_DIR.parent / "data-dashboard" / "public" / "json"
    if sibling.parent.parent.is_dir():  # data-dashboard 存在
        return sibling
    return Path(r"C:\code\data-dashboard\public\json")


DEFAULT_DEST = _default_dest()

# Retired download modules: skip mirror sync (leave existing dashboard JSON untouched).
COINGLASS_SYNC_EXCLUDE: frozenset[str] = frozenset({
    "futures-open-interest-aggregated-history",  # #3 removed; dashboard OI → open-interest-history sum
    "futures-open-interest-exchange-history-chart",  # retired; wide-table chart endpoint removed
})

# tag/output/json → public/json/tag/ (mirror only listed files; stale dashboard copies removed)
TAG_JSON_FILES: list[str] = [
    "base_asset_categories_summary.json",
]


def list_coinglass_modules(only: str | None) -> list[Path]:
    """Find coinglass module directories that have output/json/."""
    if only and only in COINGLASS_SYNC_EXCLUDE:
        sys.exit(
            f"Module {only!r} is retired and excluded from dashboard sync "
            f"(existing dashboard JSON is kept as-is)."
        )
    out: list[Path] = []
    if not COINGLASS_ROOT.exists():
        return out
    for d in sorted(COINGLASS_ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name in ("symbols", "__pycache__"):
            continue
        if d.name in COINGLASS_SYNC_EXCLUDE:
            continue
        json_dir = d / "output" / "json"
        if not json_dir.exists():
            continue
        if only and d.name != only:
            continue
        out.append(d)
    if only and not out:
        sys.exit(f"Module not found or has no output/json/: {only}")
    return out


def fmt_size(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy src onto dst without ever exposing a partially written file.

    The dashboard serves this tree live, so an in-place copy leaves a window in
    which Next.js can JSON.parse a truncated file. Write a sibling temp first and
    rename; rename is atomic within a filesystem.
    """
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def sweep_stale_temps(dst_dir: Path) -> None:
    """Drop temp files left behind by a hard-killed run."""
    for leftover in dst_dir.glob(".*.tmp"):
        leftover.unlink(missing_ok=True)


def sync_json_dir(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool) -> tuple[int, int, int, int]:
    """Mirror flat *.json: copy from src_dir, delete dst extras. Returns (copied, skipped, bytes, removed)."""
    if not src_dir.is_dir():
        return 0, 0, 0, 0

    src_files = {p.name: p for p in sorted(src_dir.glob("*.json"))}
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)

    copied = skipped = bytes_copied = removed = 0
    for name, src in src_files.items():
        dst = dst_dir / name
        src_size = src.stat().st_size
        if (not force) and dst.exists() and dst.stat().st_size == src_size:
            skipped += 1
            continue
        if dry_run:
            copied += 1
            bytes_copied += src_size
            continue
        atomic_copy(src, dst)
        copied += 1
        bytes_copied += src_size

    if dst_dir.is_dir():
        if not dry_run:
            sweep_stale_temps(dst_dir)
        for dst in dst_dir.glob("*.json"):
            if dst.name not in src_files:
                removed += 1
                if not dry_run:
                    dst.unlink(missing_ok=True)

    return copied, skipped, bytes_copied, removed


def sync_named_json_files(
    src_dir: Path,
    dst_dir: Path,
    names: list[str],
    force: bool,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """Mirror only listed filenames; remove other *.json in dst_dir."""
    if not src_dir.is_dir():
        return 0, 0, 0, 0

    allowed = set(names)
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)

    copied = skipped = bytes_copied = removed = 0
    present: set[str] = set()
    for name in names:
        src = src_dir / name
        if not src.is_file():
            continue
        present.add(name)
        dst = dst_dir / name
        src_size = src.stat().st_size
        if (not force) and dst.exists() and dst.stat().st_size == src_size:
            skipped += 1
            continue
        if dry_run:
            copied += 1
            bytes_copied += src_size
            continue
        atomic_copy(src, dst)
        copied += 1
        bytes_copied += src_size

    if dst_dir.is_dir():
        if not dry_run:
            sweep_stale_temps(dst_dir)
        for dst in dst_dir.glob("*.json"):
            if dst.name not in present:
                removed += 1
                if not dry_run:
                    dst.unlink(missing_ok=True)

    return copied, skipped, bytes_copied, removed


def format_sync_tag(
    copied: int,
    skipped: int,
    bytes_c: int,
    removed: int,
    *,
    total: int,
    src_exists: bool,
    src_label: str,
) -> str:
    if not src_exists:
        return f"source missing: {src_label}"
    if total == 0 and removed == 0:
        return "(empty)"
    parts: list[str] = []
    if copied == 0 and total > 0:
        parts.append("all already up-to-date")
    else:
        parts.append(f"copied {copied}, skipped {skipped}, {fmt_size(bytes_c)}")
    if removed:
        parts.append(f"removed {removed}")
    return "; ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help="Destination root (default: dashboard public/json)",
    )
    parser.add_argument(
        "--module",
        default=None,
        help="Copy only one coinglass module (ignored unless --only is all/coinglass)",
    )
    parser.add_argument(
        "--only",
        choices=("all", "coinglass", "treasuries", "cex", "tag", "btc-index", "options", "stablecoin", "mining-shutdown-price", "tradfi"),
        default="all",
        help="Choose dataset to copy (default: all)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite all files (no size check)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would copy without writing")
    args = parser.parse_args()

    dest_root = Path(args.dest).resolve()
    copy_coinglass = args.only in ("all", "coinglass")
    copy_treasuries = args.only in ("all", "treasuries")
    copy_cex = args.only in ("all", "cex")
    copy_tag = args.only in ("all", "tag")
    copy_btc_index = args.only in ("all", "btc-index")
    copy_options = args.only in ("all", "options")
    copy_stablecoin = args.only in ("all", "stablecoin")
    copy_mining_shutdown_price = args.only in ("all", "mining-shutdown-price")
    copy_tradfi = args.only in ("all", "tradfi")

    modules = list_coinglass_modules(args.module) if copy_coinglass else []
    treasuries_src = TREASURIES_ROOT / "output" / "json"
    cex_src = CEX_ROOT / "output" / "json"
    tag_src = TAG_ROOT / "output" / "json"
    btc_index_src = BTC_INDEX_ROOT / "output" / "json"
    options_src = OPTIONS_ROOT / "output" / "json"
    stablecoin_src = STABLECOIN_ROOT / "output" / "json"
    mining_shutdown_price_src = MINING_SHUTDOWN_PRICE_ROOT / "output" / "json"
    # tradfi 交易所直连：独立目录 tradfi-price，1:1 镜像（可安全 mirror-delete 本目录，不碰 coinglass）
    tradfi_price_src = TRADFI_ROOT / "output" / "json" / "tradfi-price"

    mode = "DRY-RUN" if args.dry_run else (
        "FORCE-OVERWRITE" if args.force else "mirror (skip same-size copy)"
    )
    print(f"source root: {SCRIPT_DIR}")
    print(f"target:      {dest_root}")
    print(f"mode:        {mode}")
    print(
        f"datasets:    coinglass={copy_coinglass} treasuries={copy_treasuries} "
        f"cex={copy_cex} tag={copy_tag} btc-index={copy_btc_index} options={copy_options} "
        f"stablecoin={copy_stablecoin} mining-shutdown-price={copy_mining_shutdown_price} "
        f"tradfi={copy_tradfi}"
    )
    print(f"modules:     {len(modules)}\n")

    total_copied = total_skipped = total_bytes = total_removed = 0
    started = time.time()

    if copy_coinglass:
        for i, mod in enumerate(modules, 1):
            src_dir = mod / "output" / "json"
            dst_dir = dest_root / mod.name
            copied, skipped, bytes_c, removed = sync_json_dir(src_dir, dst_dir, args.force, args.dry_run)
            total = copied + skipped
            tag = format_sync_tag(
                copied,
                skipped,
                bytes_c,
                removed,
                total=total,
                src_exists=src_dir.is_dir(),
                src_label=str(src_dir),
            )
            print(f"[coinglass {i:02d}/{len(modules)}] {mod.name:<45} {tag}")
            total_copied += copied
            total_skipped += skipped
            total_bytes += bytes_c
            total_removed += removed

    if copy_treasuries:
        treasuries_dst = dest_root / "crypto-treasuries"
        copied, skipped, bytes_c, removed = sync_json_dir(
            treasuries_src, treasuries_dst, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=treasuries_src.is_dir(),
            src_label=str(treasuries_src),
        )
        print(f"[treasuries] {'crypto-treasuries':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_cex:
        cex_dst = dest_root / "cex-asset-vol"
        copied, skipped, bytes_c, removed = sync_json_dir(cex_src, cex_dst, args.force, args.dry_run)
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=cex_src.is_dir(),
            src_label=str(cex_src),
        )
        print(f"[cex]        {'cex-asset-vol':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_tag:
        tag_dst = dest_root / "tag"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            tag_src, tag_dst, TAG_JSON_FILES, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=tag_src.is_dir(),
            src_label=str(tag_src),
        )
        print(f"[tag]        {'tag':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_btc_index:
        btc_index_dst = dest_root / "btc-index"
        copied = skipped = bytes_c = removed = 0
        for sub in ("coinglass", "glassnode"):
            src_sub = btc_index_src / sub
            dst_sub = btc_index_dst / sub
            if not src_sub.is_dir():
                continue
            c, s, b, r = sync_json_dir(src_sub, dst_sub, args.force, args.dry_run)
            copied += c
            skipped += s
            bytes_c += b
            removed += r
        # Drop legacy flat JSON under btc-index/ (no longer mirrored from source root).
        if btc_index_dst.is_dir() and not args.dry_run:
            for stale in btc_index_dst.glob("*.json"):
                stale.unlink(missing_ok=True)
                removed += 1
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=btc_index_src.is_dir(),
            src_label=str(btc_index_src),
        )
        print(f"[btc-index]  {'btc-index':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_options:
        options_dst = dest_root / "option-iv"
        copied, skipped, bytes_c, removed = sync_json_dir(
            options_src, options_dst, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=options_src.is_dir(),
            src_label=str(options_src),
        )
        print(f"[options]    {'options -> option-iv':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_stablecoin:
        stablecoin_dst = dest_root / "stablecoin"
        copied, skipped, bytes_c, removed = sync_json_dir(
            stablecoin_src, stablecoin_dst, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=stablecoin_src.is_dir(),
            src_label=str(stablecoin_src),
        )
        print(f"[stablecoin] {'stablecoin':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_mining_shutdown_price:
        mining_shutdown_price_dst = dest_root / "mining-shutdown-price"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            mining_shutdown_price_src,
            mining_shutdown_price_dst,
            ["mining_shutdown_price.json", "realtime_mining_shutdown_price_snapshot.json"],
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=mining_shutdown_price_src.is_dir(),
            src_label=str(mining_shutdown_price_src),
        )
        print(f"[mining-shutdown-price] {'mining-shutdown-price':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_tradfi:
        # 独立目录：只镜像 tradfi-price，mirror-delete 仅作用于 dst/tradfi-price，不影响 coinglass
        tradfi_dst = dest_root / "tradfi-price"
        copied, skipped, bytes_c, removed = sync_json_dir(
            tradfi_price_src, tradfi_dst, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=tradfi_price_src.is_dir(),
            src_label=str(tradfi_price_src),
        )
        print(f"[tradfi]     {'tradfi-price':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    elapsed = time.time() - started
    verb = "WOULD copy" if args.dry_run else "copied"
    remove_verb = "WOULD remove" if args.dry_run else "removed"
    print(f"\nDONE in {elapsed:.1f}s")
    print(f"  {verb} : {total_copied:,} files  ({fmt_size(total_bytes)})")
    print(f"  skipped (already up-to-date): {total_skipped:,}")
    print(f"  {remove_verb}: {total_removed:,} files (not in source)")
    print(f"  destination root: {dest_root}")


if __name__ == "__main__":
    main()

