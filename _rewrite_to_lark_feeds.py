#!/usr/bin/env python3
"""把 Lark 文末统一改成数据平台 /dashboard/lark-feeds/{slug}（或已有专页）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://data.wublock123.com"
FEED = f"{BASE}/dashboard/lark-feeds"

MAP: dict[str, str] = {
    "aave-v4-spokes/flash.py": f"{FEED}/aave-v4-spokes",
    "cb-stock-dex/flash.py": f"{FEED}/cb-stock-dex",
    "cb-base-stock-dex/flash.py": f"{FEED}/cb-base-stock-dex",
    "chain-app-revenue/flash.py": f"{FEED}/chain-app-revenue",
    "chain-fees-dist/flash.py": f"{FEED}/chain-fees-dist",
    "collector-crypt/flash.py": f"{FEED}/collector-crypt",
    "depin-mcap/flash.py": f"{FEED}/depin-mcap",
    "edgex-oi/flash.py": f"{FEED}/edgex-oi",
    "falconx-vault/flash.py": f"{FEED}/falconx-vault",
    "fragment/flash.py": f"{FEED}/fragment",
    "gsr-core3/flash.py": f"{FEED}/gsr-core3",
    "gua-spot/flash.py": f"{FEED}/gua-spot",
    "lab-spot/flash.py": f"{FEED}/lab-spot",
    "zec-spot/flash.py": f"{FEED}/zec-spot",
    "l2-token-mcap/flash.py": f"{FEED}/l2-token-mcap",
    "near-intents/flash.py": f"{FEED}/near-intents",
    "paradex/flash.py": f"{FEED}/paradex",
    "physical-tcg/flash.py": f"{FEED}/physical-tcg",
    "physical-tcg/monthly_flash.py": f"{FEED}/physical-tcg",
    "spark-savings/flash.py": f"{FEED}/spark-savings",
    "xrp-fees/flash.py": f"{FEED}/xrp-fees",
    "aave-llama/flash.py": f"{FEED}/aave-llama",
    "alt-top50-since-btc-ath/flash.py": f"{FEED}/alt-top50-btc-ath",
    "binance-por/flash.py": f"{FEED}/binance-por",
    "okx-por/flash.py": f"{FEED}/okx-por",
    "btc-etf-volume/flash.py": f"{BASE}/dashboard/etf/btc",
    "crypto-treasuries/book_return_flash.py": f"{FEED}/book-return",
    "lookonchain-week/flash.py": f"{FEED}/lookonchain-week",
    "protocol-revenue-monthly/flash.py": f"{FEED}/protocol-revenue-monthly",
    "robinhood-chain/rh_base_tx_flash.py": f"{BASE}/dashboard/robinhood-chain",
    "robinhood-chain/stock_compare_flash.py": f"{BASE}/dashboard/robinhood-chain",
    "spacex-ir/flash.py": f"{BASE}/dashboard/tradfi",
    "spot-break-liq/monitor.py": f"{FEED}/spot-break-liq",
    "wallet-watch/monitor.py": f"{FEED}/wallet-watch",
    "wallet-watch/monitor_yzi_ena.py": f"{FEED}/wallet-watch",
    "wallet-watch/monitor_pump_sol.py": f"{FEED}/wallet-watch",
    "protocol-revenue-ytd/flash.py": f"{FEED}/protocol-revenue-ytd",
    "arc-app-fees/flash.py": f"{FEED}/arc-app-fees",
    "defi-tvl/flash.py": f"{FEED}/defi-tvl",
    "privacy-coins/flash.py": f"{FEED}/privacy-coins",
    "xrp-oi/flash.py": f"{FEED}/xrp-oi",
    "web3-hacks/flash.py": f"{FEED}/web3-hacks",
    "stablecoin/chain_mcap_flash.py": f"{BASE}/dashboard/stablecoin-marketcap",
    "scroll-l2/flash.py": f"{FEED}/scroll-l2",
    "robinhood-usde/flash.py": f"{BASE}/dashboard/robinhood-chain",
    "rh-chain-fees/flash.py": f"{BASE}/dashboard/robinhood-chain",
    "perp-dex-oi/flash.py": f"{FEED}/perp-dex-oi",
    "dex-volume/flash.py": f"{FEED}/dex-volume",
    "bitmine-eth/flash.py": f"{FEED}/bitmine-eth",
    "uni-burn/flash.py": f"{BASE}/dashboard/burns/uni",
    "token-burns/hype_flash.py": f"{BASE}/dashboard/burns/hype",
    "etf-halfyear/flash.py": f"{BASE}/dashboard/etf",
    "x402/flash.py": f"{BASE}/dashboard/x402",
}

CONST_NAMES = (
    "PAGE",
    "PAGE_URL",
    "JSON_URL",
    "DASHBOARD_URL",
    "DASHBOARD",
    "CHART_URL",
    "NEWS_URL",
    "POR_PAGE",
)


def set_const(text: str, name: str, value: str) -> str:
    pat = re.compile(rf'^({name}\s*=\s*)(["\']).*\2', re.M)
    if pat.search(text):
        return pat.sub(rf'\1"{value}"', text, count=1)
    m = re.search(r"^(TZ_CN\s*=.*)$", text, re.M)
    if m:
        return text[: m.end()] + f'\n{name} = "{value}"' + text[m.end() :]
    m = re.search(r"^(HERE\s*=.*)$", text, re.M)
    if m:
        return text[: m.end()] + f'\n{name} = "{value}"' + text[m.end() :]
    return f'{name} = "{value}"\n' + text


def append_page_to_returns(text: str, var: str) -> str:
    if "{" + var + "}" in text:
        return text
    # return f"{title}\n{body}"
    text2 = text.replace(
        'return f"{title}\\n{body}"',
        f'return f"{{title}}\\n{{body}}\\n{{{var}}}"',
    )
    text2 = text2.replace(
        "return f'{title}\\n{body}'",
        f"return f'{{title}}\\n{{body}}\\n{{{var}}}'",
    )
    # return f"{mark}\n{title}\n{body}"
    text2 = text2.replace(
        'return f"{mark}\\n{title}\\n{body}"',
        f'return f"{{mark}}\\n{{title}}\\n{{body}}\\n{{{var}}}"',
    )
    # return body + f"\n\n{POR_PAGE}" already has const
    # return f"{title}\n{body}\n\n{POR_PAGE}"
    return text2


def rewrite_file(rel: str, url: str) -> bool:
    path = ROOT / Path(rel)
    if not path.is_file():
        print("missing", rel)
        return False
    text = path.read_text(encoding="utf-8")
    orig = text

    primary = None
    for name in CONST_NAMES:
        if re.search(rf"^{name}\s*=", text, re.M):
            primary = name
            break
    if primary is None:
        primary = "PAGE"
    text = set_const(text, primary, url)
    for name in CONST_NAMES:
        if name in ("CG_PAGE",):
            continue
        if name != primary and re.search(rf"^{name}\s*=", text, re.M):
            # don't overwrite IR_HOME etc
            if name in CONST_NAMES:
                text = set_const(text, name, url)

    if "spacex-ir" in rel:
        text = re.sub(
            r"src = str\(data\.get\(\"source_url\"\) or IR_HOME\)",
            f'src = "{url}"',
            text,
        )

    text = text.replace("\\n{PAGE}\\n{CG_PAGE}", "\\n{PAGE}")
    text = text.replace("\\n{JSON_URL}", f"\\n{{{primary}}}")
    text = append_page_to_returns(text, primary)

    # force PAGE for wallet scripts if still no footer var usage
    if "wallet-watch" in rel:
        text = set_const(text, "PAGE", url)
        if "{PAGE}" not in text:
            # append to common message assembly: lines.append or text +=
            if "lines.append(" in text and 'lines.append(PAGE)' not in text:
                text = text.replace(
                    'return "\\n".join(lines)',
                    'lines.append(PAGE)\n    return "\\n".join(lines)',
                )
                text = text.replace(
                    "return '\\n'.join(lines)",
                    "lines.append(PAGE)\n    return '\\n'.join(lines)",
                )

    if text == orig:
        print("noop", rel)
        return False
    path.write_text(text, encoding="utf-8")
    print("ok", rel)
    return True


def main() -> None:
    n = 0
    for rel, url in MAP.items():
        if rewrite_file(rel, url):
            n += 1
    print("changed", n)


if __name__ == "__main__":
    main()
