"""精确 (交易所, 原始base) 配对清理 coinglass live 的 tradfi 残留——碰撞安全。

原理:只删「交易所直连下载器在**同一交易所**上确认为 tradfi 的那个确切标的」。
配对来自 tradfi-price 每文件的 (exchange, base_asset 原样)。匹配时对 coinglass 文件
只做「去计价币/合约后缀」得到裸 base，**不剥 STOCK/X**（保留区分度）。

为什么安全:
- Binance_BB (BounceBit 币): 下载器没在 Binance 认定 BB 是股票 → (Binance,BB) 不在配对 → 不删。
- OKX_BB (BlackBerry 股): 下载器在 OKX 认定 → (OKX,BB) 在配对 → 删。
- Bitget_STX (Stacks 币) vs Bitget_STXSTOCK (Seagate 股): 原始 base 不同(STX vs STXSTOCK)，
  下载器存的是 STXSTOCK → 只删 STXSTOCK，Stacks 保留。
同一交易所同一裸 base 不会既是币又是股(同符号不重复上市)，故精确配对无碰撞。

用法:
  python _cleanup_tradfi_live.py            # dry-run
  python _cleanup_tradfi_live.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tradfi"))
import _common as T  # noqa: F401 (保留以便复用同义词，如需要)

LIVE = Path("/root/dashboard/public/json")
TRADFI_PRICE = LIVE / "tradfi-price"
COINGLASS_DIRS = ["futures-price-history", "futures-open-interest-history", "spot-price-history"]

QUOTE_SUFFIXES = ["USDT", "USDC", "BUSD", "FDUSD", "USD"]
CONTRACT_TAGS = ["_UMCBL", "_DMCBL", "_CMCBL"]

# 交易所名归一（download 与 coinglass 命名对齐）
EX_CANON = {
    "crypto-com": "crypto.com",
}


def canon_ex(ex: str) -> str:
    e = (ex or "").strip().lower()
    return EX_CANON.get(e, e)


def strip_base(raw: str) -> str:
    """去合约标签/PF_前缀/连字符段/计价币，得裸 base（不剥 STOCK/X）。"""
    s = (raw or "").upper()
    for tag in CONTRACT_TAGS:
        if s.endswith(tag):
            s = s[: -len(tag)]
    if s.startswith("PF_"):
        s = s[3:]
    for sep in ("-", "_"):
        if sep in s:
            s = s.split(sep)[0]
            break
    for q in QUOTE_SUFFIXES:
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
            break
    return s


def build_pairs() -> set[tuple[str, str]]:
    """(canon_exchange, 裸base) —— base 取 tradfi-price meta 的 base_asset 原样(仅大写)。"""
    pairs: set[tuple[str, str]] = set()
    for p in TRADFI_PRICE.glob("*.json"):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ex = canon_ex(str(meta.get("exchange") or ""))
        ba = str(meta.get("base_asset") or "").strip().upper()
        if ex and ba:
            pairs.add((ex, ba))
    return pairs


def coinglass_ex_base(stem: str) -> tuple[str, str]:
    parts = stem.split("_")
    if len(parts) < 3:
        return "", ""
    ex = canon_ex(parts[0])
    instrument = "_".join(parts[1:-1])
    return ex, strip_base(instrument)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--samples", type=int, default=12)
    args = ap.parse_args()

    pairs = build_pairs()
    print(f"tradfi 精确配对 (exchange,base): {len(pairs)} 组")

    # 碰撞安全校验:这些真加密的 (交易所,裸base) 不应出现在配对里
    guard = [("binance", "BB"), ("binance", "S"), ("bitget", "STX"), ("okx", "QNT"),
             ("binance", "BTC"), ("binance", "ETH"), ("okx", "DIA"), ("binance", "IMX")]
    for g in guard:
        print(f"  安全校验 {g} 在配对(应 False): {g in pairs}")

    total = 0
    for mod in COINGLASS_DIRS:
        d = LIVE / mod
        if not d.is_dir():
            continue
        hits = []
        for p in sorted(d.glob("*.json")):
            ex, base = coinglass_ex_base(p.stem)
            if ex and base and (ex, base) in pairs:
                hits.append(p)
        total += len(hits)
        print(f"\n[{mod}] 命中 {len(hits)} / 总 {len(list(d.glob('*.json')))}")
        for p in hits[: args.samples]:
            print(f"    {'DEL' if args.apply else 'would'}: {p.name}")
        if args.apply:
            for p in hits:
                p.unlink(missing_ok=True)

    print(f"\n{'已删' if args.apply else 'dry-run 命中'} 合计: {total}")


if __name__ == "__main__":
    main()
