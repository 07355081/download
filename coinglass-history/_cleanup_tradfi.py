"""清理 coinglass 逐标的模块下的 tradfi 存量（Stocks / Commodities / Indices）。

tradfi 已从 coinglass 完全下线，改由交易所直连独占（落 tradfi/output/json/tradfi-price）。
本脚本删除 coinglass per-instrument 模块 cache/ 与 output/json/ 里判为 tradfi 的文件，
使 coinglass 输出只剩加密标的；随后 copy_to_dashboard 的 mirror-delete 会把 dashboard
对应副本一并清掉。

判定复用 tradfi/_common.classify_sector（经 _common.is_tradfi_base）：
  - base_asset 优先取 symbols/{market}_instruments.json 中的权威值
  - 缺失（如已下架）则按文件名兜底提取 base 再判定
只有判为 Stocks/Commodities/Indices 才删除；加密标的（含 HMSTR/FARTCOIN 等）不受影响。

用法：
  python _cleanup_tradfi.py                 # dry-run，仅打印将删除的文件
  python _cleanup_tradfi.py --apply         # 实际删除
  python _cleanup_tradfi.py --module futures-price-history --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _common as C

# 模块 -> coinglass 市场（决定用哪个 instruments 清单取权威 base_asset）
MODULE_MARKET: dict[str, str] = {
    "futures-price-history": "futures",
    "futures-open-interest-history": "futures",
    "spot-price-history": "spot",
}


def load_base_map(market: str) -> dict[tuple[str, str], str]:
    """(exchange, instrument_id) -> base_asset，来自 symbols/{market}_instruments.json。"""
    path = C.SYMBOLS_DIR / f"{market}_instruments.json"
    out: dict[tuple[str, str], str] = {}
    if not path.is_file():
        return out
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    by_ex = doc.get("by_exchange") if isinstance(doc, dict) else None
    if not isinstance(by_ex, dict):
        return out
    for exchange, items in by_ex.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            iid = str(it.get("instrument_id") or "").strip()
            base = str(it.get("base_asset") or "").strip()
            if iid and base:
                out[(str(exchange), iid)] = base
    return out


def base_for_file(stem: str, base_map: dict[tuple[str, str], str]) -> str:
    """从文件名 {exchange}_{instrument}_{interval} 还原 base_asset。"""
    parts = stem.split("_")
    if len(parts) < 3:
        return ""
    exchange = parts[0]
    instrument = "_".join(parts[1:-1])
    base = base_map.get((exchange, instrument))
    if base:
        return base
    return C.extract_base_from_instrument(instrument)


def scan_dir(directory: Path, pattern: str, base_map: dict[tuple[str, str], str]) -> list[Path]:
    if not directory.is_dir():
        return []
    hits: list[Path] = []
    for p in sorted(directory.glob(pattern)):
        base = base_for_file(p.stem, base_map)
        if C.is_tradfi_base(base):
            hits.append(p)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="实际删除（缺省仅 dry-run）")
    ap.add_argument("--module", default=None, help="只处理某个模块，缺省处理所有 per-instrument 模块")
    ap.add_argument("--samples", type=int, default=15, help="dry-run 打印的样例数")
    args = ap.parse_args()

    if C._load_tradfi_classifier() is None:
        raise SystemExit("无法加载 tradfi/_common.classify_sector，终止（避免误删）。")

    modules = [args.module] if args.module else list(MODULE_MARKET.keys())
    total_hits = 0
    total_deleted = 0
    for mod in modules:
        market = MODULE_MARKET.get(mod)
        if market is None:
            print(f"[skip] 未知模块 {mod}（无 market 映射）")
            continue
        base_map = load_base_map(market)
        mod_dir = C.ROOT_DIR / mod
        cache_hits = scan_dir(mod_dir / "cache", "*.csv", base_map)
        json_hits = scan_dir(mod_dir / "output" / "json", "*.json", base_map)
        hits = cache_hits + json_hits
        total_hits += len(hits)
        print(f"\n[{mod}] market={market}  cache={len(cache_hits)}  json={len(json_hits)}  base_map={len(base_map)}")
        for p in hits[: args.samples]:
            print(f"    {'DEL' if args.apply else 'would-del'}: {p.relative_to(C.ROOT_DIR)}  (base={base_for_file(p.stem, base_map)})")
        if len(hits) > args.samples:
            print(f"    ... 其余 {len(hits) - args.samples} 个")
        if args.apply:
            for p in hits:
                try:
                    p.unlink(missing_ok=True)
                    total_deleted += 1
                except OSError as e:
                    print(f"    删除失败 {p.name}: {e}")

    print(f"\n{'已删除' if args.apply else 'dry-run 命中'} 合计: {total_deleted if args.apply else total_hits} 个文件")
    if not args.apply and total_hits:
        print("加 --apply 实际删除。")


if __name__ == "__main__":
    main()
