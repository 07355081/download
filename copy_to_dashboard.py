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
  9) tradfi exchange-direct: tradfi/output/json/tradfi-{price,oi,funding}/*.json
     mirrored 1:1 to public/json/tradfi-{price,oi,funding}/ (isolated dirs; safe
     mirror-delete per dir, never touches coinglass)

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
HYPERLIQUID_ROOT = SCRIPT_DIR / "hyperliquid"
EF_WALLETS_ROOT = SCRIPT_DIR / "ef-wallets"
ALT_LARGECAP_ROOT = SCRIPT_DIR / "alt-largecap"
UNI_BURN_ROOT = SCRIPT_DIR / "uni-burn"
TOKEN_BURNS_ROOT = SCRIPT_DIR / "token-burns"
ROBINHOOD_ROOT = SCRIPT_DIR / "robinhood-chain"
GMGN_ROOT = SCRIPT_DIR / "gmgn"
# TradFi 交易所直连输出（独立目录，与 coinglass 完全隔离）
TRADFI_ROOT = SCRIPT_DIR / "tradfi"
TRADFI_SPOT_ROOT = SCRIPT_DIR / "tradfi-spot"
STRATEGY_MSTR_ROOT = SCRIPT_DIR / "strategy-mstr"
DEX_FEES_ROOT = SCRIPT_DIR / "dex-fees"
UNISWAP_VOLUME_ROOT = SCRIPT_DIR / "uniswap-volume"
UNISWAP_REVENUE_ROOT = SCRIPT_DIR / "uniswap-revenue"
CHAIN_FEES_DIST_ROOT = SCRIPT_DIR / "chain-fees-dist"
CHAIN_APP_REVENUE_ROOT = SCRIPT_DIR / "chain-app-revenue"
ALT_TOP50_BTC_ATH_ROOT = SCRIPT_DIR / "alt-top50-since-btc-ath"
STABLECOIN_CARDS_ROOT = SCRIPT_DIR / "stablecoin-cards"
GSR_CORE3_ROOT = SCRIPT_DIR / "gsr-core3"
BTC_GOLD_ROOT = SCRIPT_DIR / "btc-gold"
DEFI_TVL_ROOT = SCRIPT_DIR / "defi-tvl"
WEB3_HACKS_ROOT = SCRIPT_DIR / "web3-hacks"
DEX_VOLUME_ROOT = SCRIPT_DIR / "dex-volume"
PERP_DEX_OI_ROOT = SCRIPT_DIR / "perp-dex-oi"
SCROLL_L2_ROOT = SCRIPT_DIR / "scroll-l2"
BITMINE_ETH_ROOT = SCRIPT_DIR / "bitmine-eth"
ROBINHOOD_USDE_ROOT = SCRIPT_DIR / "robinhood-usde"
XRP_OI_ROOT = SCRIPT_DIR / "xrp-oi"
RH_CHAIN_FEES_ROOT = SCRIPT_DIR / "rh-chain-fees"
OPTIONS_EXPIRY_ROOT = SCRIPT_DIR / "options-expiry"
ARC_APP_FEES_ROOT = SCRIPT_DIR / "arc-app-fees"
PROTOCOL_REVENUE_YTD_ROOT = SCRIPT_DIR / "protocol-revenue-ytd"
TOKEN_BUYBACK_SUPPLY_ROOT = SCRIPT_DIR / "token-buyback-supply"
PROTOCOL_REVENUE_DAILY_ROOT = SCRIPT_DIR / "protocol-revenue-daily"
PROTOCOL_REVENUE_MONTHLY_ROOT = SCRIPT_DIR / "protocol-revenue-monthly"
PERP_VOLUME_ROOT = SCRIPT_DIR / "perp-volume"
L2_TOKEN_MCAP_ROOT = SCRIPT_DIR / "l2-token-mcap"
BTC_WEEKLY_MOVE_ROOT = SCRIPT_DIR / "btc-weekly-move"
CB_PREMIUM_ROOT = SCRIPT_DIR / "cb-premium"

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

# hyperliquid/output/json → public/json/hyperliquid/ (仅镜像这几个固定 query 输出;
# 名单须与 hyperliquid/download.py 的 QUERIES 保持一致)
HYPERLIQUID_JSON_FILES: list[str] = [
    "hip3_overview.json",
    "hip3_by_category.json",
    "hip3_by_market.json",
    "hip3_by_symbol.json",
    "hl_by_category.json",
    "hip3_and_crypto.json",
    "hip4.json",
    # download_fees.py 的三个输出(费用取 DefiLlama 实测,不用 Dune 的推算列)
    "hl_fees_daily.json",
    "hl_volume_split.json",
    "hyperevm_dex.json",
    # download_paperdash.py（公开 API，不占 Dune）
    "papertrade_stats.json",
]

# uni-burn/output/json → public/json/uni-burn/ (须与 uni-burn/download.py 的 QUERIES 一致)
UNI_BURN_JSON_FILES: list[str] = [
    "daily_by_chain.json",
]

# token-burns/output/json → public/json/token-burns/
TOKEN_BURNS_JSON_FILES: list[str] = [
    "uni.json",
    "pump.json",
    "pons.json",
    "hype.json",
]

# robinhood-chain/output/json → public/json/robinhood-chain/
# (须与 robinhood-chain/download.py 的 QUERIES 及前端路由白名单一致)
ROBINHOOD_JSON_FILES: list[str] = [
    "daily_transactions.json",
    "active_wallets.json",
    "dex_volume.json",
    "launchpad_new_tokens.json",
    "launchpad_activity.json",
    "rwa_aum.json",
    "rwa_volume.json",
    "rh_fees_daily.json",
    "rh_base_txcount.json",
]

# gmgn/output/json → public/json/gmgn/（仅最新快照；history/ 留下载侧）
GMGN_JSON_FILES: list[str] = [
    "meme_markets_24h.json",
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
        choices=(
            "all", "coinglass", "treasuries", "cex", "tag", "btc-index", "options",
            "stablecoin", "mining-shutdown-price", "hyperliquid", "uni-burn", "token-burns", "robinhood-chain",
            "gmgn", "tradfi", "tradfi-spot", "tradfi-spot-live", "strategy-mstr",
            "dex-fees", "uniswap-volume", "uniswap-revenue", "chain-fees-dist", "chain-app-revenue",
            "alt-top50-since-btc-ath", "ef-wallets",
            "stablecoin-cards", "gsr-core3", "btc-gold", "defi-tvl", "web3-hacks", "dex-volume", "perp-dex-oi",
            "scroll-l2", "bitmine-eth", "robinhood-usde", "xrp-oi", "rh-chain-fees", "options-expiry", "arc-app-fees", "protocol-revenue-ytd", "token-buyback-supply",
            "protocol-revenue-daily", "protocol-revenue-monthly", "perp-volume", "alt-largecap",
            "l2-token-mcap", "btc-weekly-move", "cb-premium",
        ),
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
    copy_hyperliquid = args.only in ("all", "hyperliquid")
    copy_uni_burn = args.only in ("all", "uni-burn")
    copy_token_burns = args.only in ("all", "token-burns")
    copy_robinhood = args.only in ("all", "robinhood-chain")
    copy_gmgn = args.only in ("all", "gmgn")
    copy_tradfi = args.only in ("all", "tradfi")
    copy_tradfi_spot = args.only == "tradfi-spot"
    copy_tradfi_spot_live = args.only == "tradfi-spot-live"
    copy_strategy_mstr = args.only in ("all", "strategy-mstr")
    copy_dex_fees = args.only in ("all", "dex-fees")
    copy_uniswap_volume = args.only in ("all", "uniswap-volume")
    copy_uniswap_revenue = args.only in ("all", "uniswap-revenue")
    copy_chain_fees_dist = args.only in ("all", "chain-fees-dist")
    copy_chain_app_revenue = args.only in ("all", "chain-app-revenue")
    copy_alt_top50_btc_ath = args.only in ("all", "alt-top50-since-btc-ath")
    copy_ef_wallets = args.only in ("all", "ef-wallets")
    copy_stablecoin_cards = args.only in ("all", "stablecoin-cards")
    copy_gsr_core3 = args.only in ("all", "gsr-core3")
    copy_btc_gold = args.only in ("all", "btc-gold")
    copy_defi_tvl = args.only in ("all", "defi-tvl")
    copy_web3_hacks = args.only in ("all", "web3-hacks")
    copy_dex_volume = args.only in ("all", "dex-volume")
    copy_perp_dex_oi = args.only in ("all", "perp-dex-oi")
    copy_scroll_l2 = args.only in ("all", "scroll-l2")
    copy_bitmine_eth = args.only in ("all", "bitmine-eth")
    copy_robinhood_usde = args.only in ("all", "robinhood-usde")
    copy_xrp_oi = args.only in ("all", "xrp-oi")
    copy_rh_chain_fees = args.only in ("all", "rh-chain-fees")
    copy_options_expiry = args.only in ("all", "options-expiry")
    copy_arc_app_fees = args.only in ("all", "arc-app-fees")
    copy_protocol_revenue_ytd = args.only in ("all", "protocol-revenue-ytd")
    copy_token_buyback_supply = args.only in ("all", "token-buyback-supply")
    copy_protocol_revenue_daily = args.only in ("all", "protocol-revenue-daily")
    copy_protocol_revenue_monthly = args.only in ("all", "protocol-revenue-monthly")
    copy_perp_volume = args.only in ("all", "perp-volume")
    copy_alt_largecap = args.only in ("all", "alt-largecap")
    copy_l2_token_mcap = args.only in ("all", "l2-token-mcap")
    copy_btc_weekly_move = args.only in ("all", "btc-weekly-move")
    copy_cb_premium = args.only in ("all", "cb-premium")

    modules = list_coinglass_modules(args.module) if copy_coinglass else []
    treasuries_src = TREASURIES_ROOT / "output" / "json"
    cex_src = CEX_ROOT / "output" / "json"
    tag_src = TAG_ROOT / "output" / "json"
    btc_index_src = BTC_INDEX_ROOT / "output" / "json"
    options_src = OPTIONS_ROOT / "output" / "json"
    stablecoin_src = STABLECOIN_ROOT / "output" / "json"
    mining_shutdown_price_src = MINING_SHUTDOWN_PRICE_ROOT / "output" / "json"
    hyperliquid_src = HYPERLIQUID_ROOT / "output" / "json"
    uni_burn_src = UNI_BURN_ROOT / "output" / "json"
    token_burns_src = TOKEN_BURNS_ROOT / "output" / "json"
    robinhood_src = ROBINHOOD_ROOT / "output" / "json"
    gmgn_src = GMGN_ROOT / "output" / "json"
    # tradfi 交易所直连：price/oi/funding 各一目录，1:1 镜像（可安全 mirror-delete 本目录，不碰 coinglass）
    tradfi_json_src = TRADFI_ROOT / "output" / "json"
    tradfi_price_src = tradfi_json_src / "tradfi-price"
    tradfi_oi_src = tradfi_json_src / "tradfi-oi"
    tradfi_funding_src = tradfi_json_src / "tradfi-funding"
    tradfi_spot_json = TRADFI_SPOT_ROOT / "output" / "json"
    tradfi_spot_price_src = tradfi_spot_json / "tradfi-spot-price"
    tradfi_spot_spread_src = tradfi_spot_json / "tradfi-arb-spread"
    tradfi_spot_live_src = tradfi_spot_json / "tradfi-arb-live.json"
    tradfi_session_moves_src = tradfi_spot_json / "tradfi-session-moves.json"

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
        f"hyperliquid={copy_hyperliquid} uni-burn={copy_uni_burn} "
        f"token-burns={copy_token_burns} robinhood-chain={copy_robinhood} gmgn={copy_gmgn} "
        f"tradfi={copy_tradfi} tradfi-spot={copy_tradfi_spot} tradfi-spot-live={copy_tradfi_spot_live} "
        f"strategy-mstr={copy_strategy_mstr} dex-fees={copy_dex_fees} "
        f"uniswap-volume={copy_uniswap_volume} uniswap-revenue={copy_uniswap_revenue} "
        f"chain-fees-dist={copy_chain_fees_dist} chain-app-revenue={copy_chain_app_revenue} "
        f"alt-top50-since-btc-ath={copy_alt_top50_btc_ath} ef-wallets={copy_ef_wallets} "
        f"stablecoin-cards={copy_stablecoin_cards} gsr-core3={copy_gsr_core3} "
        f"btc-gold={copy_btc_gold} "
        f"defi-tvl={copy_defi_tvl} web3-hacks={copy_web3_hacks} "
        f"dex-volume={copy_dex_volume} "
        f"perp-dex-oi={copy_perp_dex_oi} scroll-l2={copy_scroll_l2} bitmine-eth={copy_bitmine_eth} "
        f"robinhood-usde={copy_robinhood_usde} xrp-oi={copy_xrp_oi} "
        f"rh-chain-fees={copy_rh_chain_fees} "
        f"options-expiry={copy_options_expiry} "
        f"arc-app-fees={copy_arc_app_fees} "
        f"protocol-revenue-ytd={copy_protocol_revenue_ytd} "
        f"token-buyback-supply={copy_token_buyback_supply} "
        f"protocol-revenue-daily={copy_protocol_revenue_daily} "
        f"protocol-revenue-monthly={copy_protocol_revenue_monthly} "
        f"perp-volume={copy_perp_volume} alt-largecap={copy_alt_largecap} "
        f"l2-token-mcap={copy_l2_token_mcap} btc-weekly-move={copy_btc_weekly_move} "
        f"cb-premium={copy_cb_premium}"
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
            # 源子目录缺失【或为空】都跳过: 绝不从空源 mirror-delete 掉 dashboard 子目录。
            # (VPS 默认不生成 glassnode, 其源目录常驻但为空; 此前正是空源触发 mirror-delete
            #  把 dashboard 的 glassnode 数据全部误删。)
            if not src_sub.is_dir() or not any(src_sub.glob("*.json")):
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

    if copy_hyperliquid:
        hyperliquid_dst = dest_root / "hyperliquid"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            hyperliquid_src,
            hyperliquid_dst,
            HYPERLIQUID_JSON_FILES,
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=hyperliquid_src.is_dir(),
            src_label=str(hyperliquid_src),
        )
        print(f"[hyperliquid] {'hyperliquid':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_uni_burn:
        uni_burn_dst = dest_root / "uni-burn"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            uni_burn_src,
            uni_burn_dst,
            UNI_BURN_JSON_FILES,
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=uni_burn_src.is_dir(),
            src_label=str(uni_burn_src),
        )
        print(f"[uni-burn] {'uni-burn':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_token_burns:
        token_burns_dst = dest_root / "token-burns"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            token_burns_src,
            token_burns_dst,
            TOKEN_BURNS_JSON_FILES,
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=token_burns_src.is_dir(),
            src_label=str(token_burns_src),
        )
        print(f"[token-burns] {'token-burns':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_robinhood:
        robinhood_dst = dest_root / "robinhood-chain"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            robinhood_src,
            robinhood_dst,
            ROBINHOOD_JSON_FILES,
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=robinhood_src.is_dir(),
            src_label=str(robinhood_src),
        )
        print(f"[robinhood-chain] {'robinhood-chain':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_gmgn:
        gmgn_dst = dest_root / "gmgn"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            gmgn_src,
            gmgn_dst,
            GMGN_JSON_FILES,
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=gmgn_src.is_dir(),
            src_label=str(gmgn_src),
        )
        print(f"[gmgn] {'gmgn':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_strategy_mstr:
        # strategy-mstr: 独立目录的三份扁平 json(latest / mnav_history / net_reserve_history),
        # 日快照留在 download 侧 output/json/snapshots, 不上前端。
        strategy_src = STRATEGY_MSTR_ROOT / "output" / "json"
        strategy_dst = dest_root / "strategy-mstr"
        copied, skipped, bytes_c, removed = sync_json_dir(
            strategy_src, strategy_dst, args.force, args.dry_run
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=strategy_src.is_dir(),
            src_label=str(strategy_src),
        )
        print(f"[strategy-mstr] {'strategy-mstr':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_tradfi:
        # 独立目录：price / oi / funding 分别镜像，mirror-delete 只作用于各 dst 子目录
        for label, src in (
            ("tradfi-price", tradfi_price_src),
            ("tradfi-oi", tradfi_oi_src),
            ("tradfi-funding", tradfi_funding_src),
        ):
            dst = dest_root / label
            copied, skipped, bytes_c, removed = sync_json_dir(
                src, dst, args.force, args.dry_run
            )
            tag = format_sync_tag(
                copied,
                skipped,
                bytes_c,
                removed,
                total=copied + skipped,
                src_exists=src.is_dir(),
                src_label=str(src),
            )
            print(f"[tradfi]     {label:<45} {tag}")
            total_copied += copied
            total_skipped += skipped
            total_bytes += bytes_c
            total_removed += removed

    if copy_tradfi_spot_live:
        dst = dest_root / "tradfi-arb-live.json"
        if tradfi_spot_live_src.is_file():
            src_size = tradfi_spot_live_src.stat().st_size
            if args.dry_run:
                print(f"[tradfi-spot-live] tradfi-arb-live.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    atomic_copy(tradfi_spot_live_src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[tradfi-spot-live] tradfi-arb-live.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[tradfi-spot-live] tradfi-arb-live.json skipped (same size)")
        else:
            print(f"[tradfi-spot-live] missing source: {tradfi_spot_live_src}")

        uni_src = tradfi_spot_json / "tradfi-spot-universe.json"
        uni_dst = dest_root / "tradfi-spot-universe.json"
        if uni_src.is_file():
            src_size = uni_src.stat().st_size
            if args.dry_run:
                print(f"[tradfi-spot-live] tradfi-spot-universe.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not uni_dst.exists()) or uni_dst.stat().st_size != src_size:
                if not args.dry_run:
                    uni_dst.parent.mkdir(parents=True, exist_ok=True)
                    atomic_copy(uni_src, uni_dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[tradfi-spot-live] tradfi-spot-universe.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[tradfi-spot-live] tradfi-spot-universe.json skipped (same size)")
        else:
            print(f"[tradfi-spot-live] missing source: {uni_src}")

        moves_dst = dest_root / "tradfi-session-moves.json"
        if tradfi_session_moves_src.is_file():
            src_size = tradfi_session_moves_src.stat().st_size
            if args.dry_run:
                print(f"[tradfi-spot-live] tradfi-session-moves.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not moves_dst.exists()) or moves_dst.stat().st_size != src_size:
                if not args.dry_run:
                    moves_dst.parent.mkdir(parents=True, exist_ok=True)
                    atomic_copy(tradfi_session_moves_src, moves_dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[tradfi-spot-live] tradfi-session-moves.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[tradfi-spot-live] tradfi-session-moves.json skipped (same size)")
        else:
            print(f"[tradfi-spot-live] missing source: {tradfi_session_moves_src}")

        # Per-day archives for US-session threshold history backfill.
        moves_hist_src = tradfi_spot_json / "tradfi-session-moves-history"
        moves_hist_dst = dest_root / "tradfi-session-moves-history"
        if moves_hist_src.is_dir():
            for src in sorted(moves_hist_src.glob("????-??-??.json")):
                dst = moves_hist_dst / src.name
                src_size = src.stat().st_size
                if args.dry_run:
                    print(f"[tradfi-spot-live] {src.name} WOULD copy ({fmt_size(src_size)})")
                    total_copied += 1
                    total_bytes += src_size
                elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                    if not args.dry_run:
                        moves_hist_dst.mkdir(parents=True, exist_ok=True)
                        atomic_copy(src, dst)
                    total_copied += 1
                    total_bytes += src_size
                    print(f"[tradfi-spot-live] history/{src.name} copied ({fmt_size(src_size)})")
                else:
                    total_skipped += 1
        else:
            print(f"[tradfi-spot-live] missing source: {moves_hist_src}")

        for legacy in ("tradfi-spot-price", "tradfi-arb-spread"):
            legacy_dir = dest_root / legacy
            if not legacy_dir.is_dir():
                continue
            n = sum(1 for p in legacy_dir.rglob("*") if p.is_file())
            if args.dry_run:
                print(f"[tradfi-spot-live] {legacy}/ WOULD remove ({n} files)")
                total_removed += n
            else:
                shutil.rmtree(legacy_dir)
                total_removed += n
                print(f"[tradfi-spot-live] {legacy}/ removed ({n} files)")

    if copy_tradfi_spot:
        for label, src_dir, dst_name in (
            ("tradfi-spot-price", tradfi_spot_price_src, "tradfi-spot-price"),
            ("tradfi-arb-spread", tradfi_spot_spread_src, "tradfi-arb-spread"),
        ):
            dst_dir = dest_root / dst_name
            copied, skipped, bytes_c, removed = sync_json_dir(
                src_dir, dst_dir, args.force, args.dry_run
            )
            tag = format_sync_tag(
                copied, skipped, bytes_c, removed,
                total=copied + skipped,
                src_exists=src_dir.is_dir(),
                src_label=str(src_dir),
            )
            print(f"[tradfi-spot] {label:<45} {tag}")
            total_copied += copied
            total_skipped += skipped
            total_bytes += bytes_c
            total_removed += removed
        uni_src = tradfi_spot_json / "tradfi-spot-universe.json"
        uni_dst = dest_root / "tradfi-spot-universe.json"
        if uni_src.is_file():
            src_size = uni_src.stat().st_size
            if args.dry_run:
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not uni_dst.exists()) or uni_dst.stat().st_size != src_size:
                if not args.dry_run:
                    uni_dst.parent.mkdir(parents=True, exist_ok=True)
                    atomic_copy(uni_src, uni_dst)
                total_copied += 1
                total_bytes += src_size
            else:
                total_skipped += 1

    if copy_dex_fees:
        src = DEX_FEES_ROOT / "output" / "json" / "dex_fees_monthly.json"
        dst = dest_root / "dex_fees_monthly.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[dex-fees] dex_fees_monthly.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[dex-fees] dex_fees_monthly.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[dex-fees] dex_fees_monthly.json skipped (same size)")
        else:
            print(f"[dex-fees] missing source: {src}")

    if copy_uniswap_volume:
        src = UNISWAP_VOLUME_ROOT / "output" / "json" / "uniswap_version_volume.json"
        dst = dest_root / "uniswap_version_volume.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[uniswap-volume] uniswap_version_volume.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[uniswap-volume] uniswap_version_volume.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[uniswap-volume] uniswap_version_volume.json skipped (same size)")
        else:
            print(f"[uniswap-volume] missing source: {src}")

    if copy_uniswap_revenue:
        src = UNISWAP_REVENUE_ROOT / "output" / "json" / "uni_revenue_by_chain.json"
        dst = dest_root / "uni_revenue_by_chain.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[uniswap-revenue] uni_revenue_by_chain.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[uniswap-revenue] uni_revenue_by_chain.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[uniswap-revenue] uni_revenue_by_chain.json skipped (same size)")
        else:
            print(f"[uniswap-revenue] missing source: {src}")

    if copy_chain_fees_dist:
        src = CHAIN_FEES_DIST_ROOT / "output" / "json" / "chain_fees_dist_latest.json"
        dst = dest_root / "chain_fees_dist_latest.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[chain-fees-dist] chain_fees_dist_latest.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size or src.stat().st_mtime > dst.stat().st_mtime + 1:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[chain-fees-dist] chain_fees_dist_latest.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[chain-fees-dist] chain_fees_dist_latest.json skipped (same size)")
        else:
            print(f"[chain-fees-dist] missing source: {src}")
        hist_src = CHAIN_FEES_DIST_ROOT / "output" / "json" / "chain_fees_dist_history"
        hist_dst = dest_root / "chain_fees_dist_history"
        if hist_src.is_dir():
            for src_file in sorted(hist_src.glob("????-??-??.json")):
                dst_file = hist_dst / src_file.name
                src_size = src_file.stat().st_size
                if args.dry_run:
                    total_copied += 1
                    total_bytes += src_size
                elif args.force or (not dst_file.exists()) or dst_file.stat().st_size != src_size or src_file.stat().st_mtime > dst_file.stat().st_mtime + 1:
                    hist_dst.mkdir(parents=True, exist_ok=True)
                    atomic_copy(src_file, dst_file)
                    total_copied += 1
                    total_bytes += src_size
                else:
                    total_skipped += 1

    if copy_chain_app_revenue:
        src = CHAIN_APP_REVENUE_ROOT / "output" / "json" / "chain_app_revenue_latest.json"
        dst = dest_root / "chain_app_revenue_latest.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[chain-app-revenue] chain_app_revenue_latest.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[chain-app-revenue] chain_app_revenue_latest.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[chain-app-revenue] chain_app_revenue_latest.json skipped (same size)")
        else:
            print(f"[chain-app-revenue] missing source: {src}")
        hist_src = CHAIN_APP_REVENUE_ROOT / "output" / "json" / "chain_app_revenue_history"
        hist_dst = dest_root / "chain_app_revenue_history"
        if hist_src.is_dir():
            for src_file in sorted(hist_src.glob("????-??-??.json")):
                dst_file = hist_dst / src_file.name
                src_size = src_file.stat().st_size
                if args.dry_run:
                    total_copied += 1
                    total_bytes += src_size
                elif args.force or (not dst_file.exists()) or dst_file.stat().st_size != src_size:
                    hist_dst.mkdir(parents=True, exist_ok=True)
                    atomic_copy(src_file, dst_file)
                    total_copied += 1
                    total_bytes += src_size
                else:
                    total_skipped += 1

    if copy_alt_top50_btc_ath:
        src = ALT_TOP50_BTC_ATH_ROOT / "output" / "json" / "alt_top50_btc_ath_latest.json"
        dst = dest_root / "alt_top50_btc_ath_latest.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(
                    f"[alt-top50-since-btc-ath] alt_top50_btc_ath_latest.json "
                    f"WOULD copy ({fmt_size(src_size)})"
                )
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(
                    f"[alt-top50-since-btc-ath] alt_top50_btc_ath_latest.json "
                    f"copied ({fmt_size(src_size)})"
                )
            else:
                total_skipped += 1
                print("[alt-top50-since-btc-ath] alt_top50_btc_ath_latest.json skipped (same size)")
        else:
            print(f"[alt-top50-since-btc-ath] missing source: {src}")
        hist_src = ALT_TOP50_BTC_ATH_ROOT / "output" / "json" / "alt_top50_btc_ath_history"
        hist_dst = dest_root / "alt_top50_btc_ath_history"
        if hist_src.is_dir():
            for src_file in sorted(hist_src.glob("????-??-??.json")):
                dst_file = hist_dst / src_file.name
                src_size = src_file.stat().st_size
                if args.dry_run:
                    total_copied += 1
                    total_bytes += src_size
                elif args.force or (not dst_file.exists()) or dst_file.stat().st_size != src_size:
                    hist_dst.mkdir(parents=True, exist_ok=True)
                    atomic_copy(src_file, dst_file)
                    total_copied += 1
                    total_bytes += src_size
                else:
                    total_skipped += 1

    if copy_stablecoin_cards:
        src = STABLECOIN_CARDS_ROOT / "output" / "json" / "stablecoin_card_volume.json"
        dst = dest_root / "stablecoin_card_volume.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[stablecoin-cards] stablecoin_card_volume.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[stablecoin-cards] stablecoin_card_volume.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[stablecoin-cards] stablecoin_card_volume.json skipped (same size)")
        else:
            print(f"[stablecoin-cards] missing source: {src}")

    if copy_gsr_core3:
        src = GSR_CORE3_ROOT / "output" / "json" / "gsr_core3.json"
        dst = dest_root / "gsr_core3.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[gsr-core3] gsr_core3.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[gsr-core3] gsr_core3.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[gsr-core3] gsr_core3.json skipped (same size)")
        else:
            print(f"[gsr-core3] missing source: {src}")

    if copy_btc_gold:
        src = BTC_GOLD_ROOT / "output" / "json" / "btc_gold_ratio.json"
        dst = dest_root / "btc_gold_ratio.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[btc-gold] btc_gold_ratio.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[btc-gold] btc_gold_ratio.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[btc-gold] btc_gold_ratio.json skipped (same size)")
        else:
            print(f"[btc-gold] missing source: {src}")

    if copy_defi_tvl:
        src = DEFI_TVL_ROOT / "output" / "json" / "defi_tvl.json"
        dst = dest_root / "defi-tvl" / "defi_tvl.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[defi-tvl] defi_tvl.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[defi-tvl] defi_tvl.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[defi-tvl] defi_tvl.json skipped (same size)")
        else:
            print(f"[defi-tvl] missing source: {src}")

    if copy_web3_hacks:
        src = WEB3_HACKS_ROOT / "output" / "json" / "web3_hacks.json"
        dst = dest_root / "web3-hacks" / "web3_hacks.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[web3-hacks] web3_hacks.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[web3-hacks] web3_hacks.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[web3-hacks] web3_hacks.json skipped (same size)")
        else:
            print(f"[web3-hacks] missing source: {src}")

    if copy_dex_volume:
        src = DEX_VOLUME_ROOT / "output" / "json" / "dex_volume.json"
        dst = dest_root / "dex-volume" / "dex_volume.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[dex-volume] dex_volume.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[dex-volume] dex_volume.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[dex-volume] dex_volume.json skipped (same size)")
        else:
            print(f"[dex-volume] missing source: {src}")

    if copy_perp_dex_oi:
        src = PERP_DEX_OI_ROOT / "output" / "json" / "perp_dex_oi.json"
        dst = dest_root / "perp-dex-oi" / "perp_dex_oi.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[perp-dex-oi] perp_dex_oi.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[perp-dex-oi] perp_dex_oi.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[perp-dex-oi] perp_dex_oi.json skipped (same size)")
        else:
            print(f"[perp-dex-oi] missing source: {src}")

    if copy_scroll_l2:
        src = SCROLL_L2_ROOT / "output" / "json" / "scroll_l2.json"
        dst = dest_root / "scroll-l2" / "scroll_l2.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[scroll-l2] scroll_l2.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[scroll-l2] scroll_l2.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[scroll-l2] scroll_l2.json skipped (same size)")
        else:
            print(f"[scroll-l2] missing source: {src}")

    if copy_bitmine_eth:
        src = BITMINE_ETH_ROOT / "output" / "json" / "bitmine_eth.json"
        dst = dest_root / "bitmine-eth" / "bitmine_eth.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[bitmine-eth] bitmine_eth.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[bitmine-eth] bitmine_eth.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[bitmine-eth] bitmine_eth.json skipped (same size)")
        else:
            print(f"[bitmine-eth] missing source: {src}")

    if copy_robinhood_usde:
        src = ROBINHOOD_USDE_ROOT / "output" / "json" / "robinhood_usde.json"
        dst = dest_root / "robinhood-usde" / "robinhood_usde.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[robinhood-usde] robinhood_usde.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[robinhood-usde] robinhood_usde.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[robinhood-usde] robinhood_usde.json skipped (same size)")
        else:
            print(f"[robinhood-usde] missing source: {src}")

    if copy_xrp_oi:
        src = XRP_OI_ROOT / "output" / "json" / "xrp_oi.json"
        dst = dest_root / "xrp-oi" / "xrp_oi.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[xrp-oi] xrp_oi.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[xrp-oi] xrp_oi.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[xrp-oi] xrp_oi.json skipped (same size)")
        else:
            print(f"[xrp-oi] missing source: {src}")

    if copy_rh_chain_fees:
        src = RH_CHAIN_FEES_ROOT / "output" / "json" / "rh_chain_fees.json"
        dst = dest_root / "rh-chain-fees" / "rh_chain_fees.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[rh-chain-fees] rh_chain_fees.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[rh-chain-fees] rh_chain_fees.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[rh-chain-fees] rh_chain_fees.json skipped (same size)")
        else:
            print(f"[rh-chain-fees] missing source: {src}")

    if copy_options_expiry:
        src = OPTIONS_EXPIRY_ROOT / "output" / "json" / "options_expiry.json"
        dst = dest_root / "options-expiry" / "options_expiry.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[options-expiry] options_expiry.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[options-expiry] options_expiry.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[options-expiry] options_expiry.json skipped (same size)")
        else:
            print(f"[options-expiry] missing source: {src}")

    if copy_arc_app_fees:
        src = ARC_APP_FEES_ROOT / "output" / "json" / "arc_app_fees.json"
        dst = dest_root / "arc-app-fees" / "arc_app_fees.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[arc-app-fees] arc_app_fees.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[arc-app-fees] arc_app_fees.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[arc-app-fees] arc_app_fees.json skipped (same size)")
        else:
            print(f"[arc-app-fees] missing source: {src}")

    if copy_token_buyback_supply:
        src = TOKEN_BUYBACK_SUPPLY_ROOT / "output" / "json" / "ytd.json"
        dst = dest_root / "token-buyback-supply" / "ytd.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[token-buyback-supply] ytd.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[token-buyback-supply] ytd.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[token-buyback-supply] ytd.json skipped (same size)")
        else:
            print(f"[token-buyback-supply] missing source: {src}")

    if copy_protocol_revenue_ytd:
        src = PROTOCOL_REVENUE_YTD_ROOT / "output" / "json" / "protocol_revenue_ytd.json"
        dst = dest_root / "protocol-revenue-ytd" / "protocol_revenue_ytd.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(
                    f"[protocol-revenue-ytd] protocol_revenue_ytd.json WOULD copy ({fmt_size(src_size)})"
                )
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(
                    f"[protocol-revenue-ytd] protocol_revenue_ytd.json copied ({fmt_size(src_size)})"
                )
            else:
                total_skipped += 1
                print("[protocol-revenue-ytd] protocol_revenue_ytd.json skipped (same size)")
        else:
            print(f"[protocol-revenue-ytd] missing source: {src}")

    if copy_protocol_revenue_daily:
        src = PROTOCOL_REVENUE_DAILY_ROOT / "output" / "json" / "protocol_revenue_daily.json"
        dst = dest_root / "protocol-revenue-daily" / "protocol_revenue_daily.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(
                    f"[protocol-revenue-daily] protocol_revenue_daily.json WOULD copy ({fmt_size(src_size)})"
                )
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(
                    f"[protocol-revenue-daily] protocol_revenue_daily.json copied ({fmt_size(src_size)})"
                )
            else:
                total_skipped += 1
                print("[protocol-revenue-daily] protocol_revenue_daily.json skipped (same size)")
        else:
            print(f"[protocol-revenue-daily] missing source: {src}")

    if copy_protocol_revenue_monthly:
        src = PROTOCOL_REVENUE_MONTHLY_ROOT / "output" / "json" / "protocol_revenue_monthly.json"
        dst = dest_root / "protocol-revenue-monthly" / "protocol_revenue_monthly.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(
                    f"[protocol-revenue-monthly] protocol_revenue_monthly.json WOULD copy ({fmt_size(src_size)})"
                )
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(
                    f"[protocol-revenue-monthly] protocol_revenue_monthly.json copied ({fmt_size(src_size)})"
                )
            else:
                total_skipped += 1
                print("[protocol-revenue-monthly] protocol_revenue_monthly.json skipped (same size)")
        else:
            print(f"[protocol-revenue-monthly] missing source: {src}")

    if copy_perp_volume:
        src = PERP_VOLUME_ROOT / "output" / "json" / "perp_volume.json"
        dst = dest_root / "perp-volume" / "perp_volume.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[perp-volume] perp_volume.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[perp-volume] perp_volume.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[perp-volume] perp_volume.json skipped (same size)")
        else:
            print(f"[perp-volume] missing source: {src}")

    if copy_alt_largecap:
        alt_src = ALT_LARGECAP_ROOT / "output" / "json"
        alt_dst = dest_root / "alt-largecap"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            alt_src,
            alt_dst,
            ["monthly.json"],
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=alt_src.is_dir(),
            src_label=str(alt_src),
        )
        print(f"[alt-largecap] {'monthly.json':<45} {tag}")
        total_copied += copied
        total_skipped += skipped
        total_bytes += bytes_c
        total_removed += removed

    if copy_l2_token_mcap:
        src = L2_TOKEN_MCAP_ROOT / "output" / "json" / "l2_token_mcap.json"
        dst = dest_root / "l2-token-mcap" / "l2_token_mcap.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[l2-token-mcap] l2_token_mcap.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[l2-token-mcap] l2_token_mcap.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[l2-token-mcap] l2_token_mcap.json skipped (same size)")
        else:
            print(f"[l2-token-mcap] missing source: {src}")

    if copy_btc_weekly_move:
        src = BTC_WEEKLY_MOVE_ROOT / "output" / "json" / "btc_weekly_move.json"
        dst = dest_root / "btc-weekly-move" / "btc_weekly_move.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[btc-weekly-move] btc_weekly_move.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[btc-weekly-move] btc_weekly_move.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[btc-weekly-move] btc_weekly_move.json skipped (same size)")
        else:
            print(f"[btc-weekly-move] missing source: {src}")

    if copy_cb_premium:
        src = CB_PREMIUM_ROOT / "output" / "json" / "cb_premium.json"
        dst = dest_root / "cb-premium" / "cb_premium.json"
        if src.is_file():
            src_size = src.stat().st_size
            if args.dry_run:
                print(f"[cb-premium] cb_premium.json WOULD copy ({fmt_size(src_size)})")
                total_copied += 1
                total_bytes += src_size
            elif args.force or (not dst.exists()) or dst.stat().st_size != src_size:
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dst)
                total_copied += 1
                total_bytes += src_size
                print(f"[cb-premium] cb_premium.json copied ({fmt_size(src_size)})")
            else:
                total_skipped += 1
                print("[cb-premium] cb_premium.json skipped (same size)")
        else:
            print(f"[cb-premium] missing source: {src}")

    if copy_ef_wallets:
        ef_src = EF_WALLETS_ROOT / "output" / "json"
        ef_dst = dest_root / "ef-wallets"
        copied, skipped, bytes_c, removed = sync_named_json_files(
            ef_src,
            ef_dst,
            ["transfers.json"],
            args.force,
            args.dry_run,
        )
        tag = format_sync_tag(
            copied,
            skipped,
            bytes_c,
            removed,
            total=copied + skipped,
            src_exists=ef_src.is_dir(),
            src_label=str(ef_src),
        )
        print(f"[ef-wallets] {'transfers.json':<45} {tag}")
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

