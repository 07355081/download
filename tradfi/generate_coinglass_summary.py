"""从 coinglass-history/symbols 生成 TradFi 合约统计 JSON。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYMBOLS = ROOT.parent / "coinglass-history" / "symbols"
OUT = ROOT / "coinglass_tradfi_instruments.json"

EXCHANGES = (
    "Binance", "Gate", "Coinbase", "Bybit", "OKX", "Bitget", "MEXC",
    "Crypto.com", "Kucoin", "HTX", "Kraken", "Bitfinex", "Upbit", "Deribit",
)

INDEX = {"SPX", "SPX500", "NAS100", "US500", "US100", "HK50", "JP225", "UK100", "DE40", "DJI", "NDX"}
METAL = {"XAU", "XAG", "XPT", "XPD", "XCU", "PAXG", "XAUT", "GOLD", "SILVER"}
OIL = {"OIL", "USOIL", "UKOIL", "WTI", "BRENT", "CL", "BZ", "NG"}
FOREX = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EUR", "GBP", "JPY", "AUD", "CHF", "NZD", "CAD"}
KNOWN_STOCKS = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "COIN", "MSTR",
    "AMD", "INTC", "NFLX", "BABA", "QQQ", "SPY", "TSLAX", "AAPLX", "NVDAX",
}


def classify(inst: dict) -> list[str] | None:
    ba = (inst.get("base_asset") or "").upper()
    iid = (inst.get("instrument_id") or "").upper()
    cats: list[str] = []
    if ba in INDEX or any(x in iid for x in ("SPX", "NAS100", "HK50", "US500", "US100")):
        cats.append("index")
    if ba in METAL or any(x in ba for x in ("XAU", "XAG", "PAXG", "XAUT", "GOLD", "SILVER")):
        cats.append("metal")
    if ba in OIL or any(x in iid for x in ("OIL", "WTI", "BRENT", "CL")):
        cats.append("oil")
    if ba in FOREX or "EURUSD" in iid or "GBPUSD" in iid or ba in ("EUR", "GBP", "JPY", "AUD", "CHF"):
        cats.append("forex")
    if ba.endswith("STOCK") or "STOCK" in ba or ba in KNOWN_STOCKS or any(
        x in iid for x in ("STOCK", "AAPL", "TSLA", "NVDA", "AMDSTOCK", "BBSTOCK")
    ):
        cats.append("stock")
    return cats or None


def main() -> None:
    out: dict = {}
    for market in ("futures", "spot"):
        doc = json.loads((SYMBOLS / f"{market}_instruments.json").read_text(encoding="utf-8"))
        for ex in EXCHANGES:
            items = doc.get("by_exchange", {}).get(ex, [])
            by_cat: dict[str, list[str]] = defaultdict(list)
            for inst in items:
                cats = classify(inst)
                if not cats:
                    continue
                for c in cats:
                    by_cat[c].append(inst["instrument_id"])
            if by_cat:
                out.setdefault(ex, {})[market] = {
                    k: {"count": len(v), "samples": v[:10]} for k, v in sorted(by_cat.items())
                }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
