# -*- coding: utf-8 -*-
"""工作记录：Lark 文末 raw JSON 改为看板/看图页。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl

folder = Path("e:/Code") / "\u5de5\u4f5c\u8bb0\u5f55"
ext = folder / "\u5bf9\u5916\u5de5\u4f5c\u8bb0\u5f55.xlsx"
inn = folder / "\u5bf9\u5185\u5de5\u4f5c\u8bb0\u5f55.xlsx"
today = "2026-10-22"
note = (
    "2026-10-22 Lark \u6587\u672b\u4e0d\u518d\u8d34 raw JSON\uff1a"
    "\u6709\u770b\u677f\u7684\u6539\u6302 /dashboard/\u2026\uff1b"
    "\u65e0\u770b\u677f\u7684\u6539\u6302 /json/\u2026/chart.html\u3002"
    "\u5df2\u6279\u91cf\u6539 37 \u4e2a flash/monitor\uff0c"
    "misc \u8865 chart \u955c\u50cf\u3002"
)

# --- \u5bf9\u5916 ---
wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "Lark \u6587\u672b\u94fe\u63a5\uff1araw JSON \u2192 \u770b\u677f/\u770b\u56fe"
ws.cell(r, 2).value = today.replace("-", "")
ws.cell(r, 3).value = "\u5df2\u5b8c\u6210\u3002\u6587\u672b\u53ea\u7559\u53ef\u70b9\u5f00\u7684\u770b\u677f\u6216 chart.html\u3002"
ws.cell(r, 4).value = "P2"
ws.cell(r, 5).value = "\u5de5\u5177/\u4f53\u9a8c"
ws.cell(r, 6).value = "data-download flash \u811a\u6ce8 + run_misc chart \u955c\u50cf"
ws.cell(r, 7).value = note
wb.save(ext)
print("external", r)

# --- \u5bf9\u5185\uff1a\u89c4\u5219\u884c\u8ffd\u8bb0 + \u66f4\u65b0\u300c\u7f51\u9875\u300d\u5217\u4ecd\u5199 JSON \u7684\u5df2\u6539\u6a21\u5757 ---
MAP = {
    "Layer 2": "https://data.wublock123.com/json/l2-token-mcap/chart.html",
    "\u6bd4\u7279\u5e01\u5355\u5468": "https://data.wublock123.com/dashboard/btc-indicators",
    "Strive": "https://data.wublock123.com/dashboard/treasuries",
    "aave-v4": "https://data.wublock123.com/json/aave-v4-spokes/chart.html",
    "Aave V4": "https://data.wublock123.com/json/aave-v4-spokes/chart.html",
    "Physical TCG": "https://data.wublock123.com/json/physical-tcg/chart.html",
    "Collector Crypt": "https://data.wublock123.com/json/collector-crypt/chart.html",
    "FalconX": "https://data.wublock123.com/json/falconx-vault/chart.html",
    "edgeX": "https://data.wublock123.com/json/edgex-oi/chart.html",
    "DePIN": "https://data.wublock123.com/json/depin-mcap/chart.html",
    "XRP": "https://data.wublock123.com/json/xrp-fees/chart.html",  # careful - may match xrp-oi
    "GUA": "https://data.wublock123.com/json/gua-spot/chart.html",
    "LAB": "https://data.wublock123.com/json/lab-spot/chart.html",
    "ZEC": "https://data.wublock123.com/json/zec-spot/chart.html",
    "Coinbase": "https://data.wublock123.com/dashboard/btc-indicators",
    "\u6ea2\u4ef7": "https://data.wublock123.com/dashboard/btc-indicators",
    "RLUSD": "https://data.wublock123.com/dashboard/stablecoin-marketcap",
    "PropAMM": "https://data.wublock123.com/dashboard/dex-fees",
    "Prop AMM": "https://data.wublock123.com/dashboard/dex-fees",
    "\u65e9\u671f\u878d\u8d44": "https://data.wublock123.com/dashboard/financing-news",
    "ETF \u534a\u5e74": "https://data.wublock123.com/dashboard/etf/btc",
    "BTC ETF \u65e5\u6210\u4ea4": "https://data.wublock123.com/dashboard/etf/btc",
    "\u7b97\u529b": "https://data.wublock123.com/dashboard/mining_shutdown",
    "Puell": "https://data.wublock123.com/dashboard/mining_shutdown",
    "\u97e9\u56fd CEX": "https://data.wublock123.com/dashboard/exchange_volume",
    "\u5e01\u5b89\u4eba\u751f": "https://data.wublock123.com/dashboard/meme-markets",
    "SpaceX": "https://data.wublock123.com/dashboard/tradfi",
    "SPCX": "https://data.wublock123.com/dashboard/tradfi",
    "STRC": "https://data.wublock123.com/dashboard/treasuries",
    "\u4ee3\u5e01\u5316\u56fd\u503a": "https://data.wublock123.com/dashboard/treasuries",
    "tokenized": "https://data.wublock123.com/dashboard/treasuries",
    "BNB \u73b0\u8d27": "https://data.wublock123.com/dashboard/exchange_volume",
    "HYPE \u73b0\u8d27": "https://data.wublock123.com/dashboard/hyperliquid",
    "\u9884\u6d4b\u5e02\u573a\u5468": "https://data.wublock123.com/dashboard/prediction-markets",
    "pm-weekly": "https://data.wublock123.com/dashboard/prediction-markets",
    "Robinhood.*Meme|RH Meme|\u4e3b\u8981 Meme.*\u5e02\u503c": "https://data.wublock123.com/dashboard/robinhood-chain",
}

wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
# \u89c4\u5219\u884c
rules = str(ws2.cell(2, 1).value or "")
if "Lark \u6587\u672b\u4e0d\u518d\u8d34 raw JSON" not in rules:
    ws2.cell(2, 1).value = (rules.rstrip() + " " + note).strip()
    print("rules updated")

updated = 0
# Only rewrite \u7f51\u9875 when it still points at raw .json and we have a mapped page
for i in range(4, ws2.max_row + 1):
    name = str(ws2.cell(i, 1).value or "")
    web = str(ws2.cell(i, 8).value or "")
    if not name:
        continue
    if ".json" not in web.lower() and "json/" not in web.lower():
        continue
    # skip if already chart/dashboard
    if "chart.html" in web or "/dashboard/" in web:
        # still may have dual JSON+dashboard; leave if dashboard present
        if "/dashboard/" in web and ".json" in web:
            # strip json mention — keep dashboard
            pass
        else:
            continue
    target = None
    # precise overrides
    if "Layer 2" in name or "l2-token" in name.lower():
        target = MAP["Layer 2"]
    elif "\u5355\u5468\u7f8e\u5143" in name or "btc-weekly" in name.lower():
        target = MAP["\u6bd4\u7279\u5e01\u5355\u5468"]
    elif "Strive" in name:
        target = MAP["Strive"]
    elif "Aave V4" in name or "aave-v4" in name.lower():
        target = MAP["Aave V4"]
    elif "Physical TCG" in name:
        target = MAP["Physical TCG"]
    elif "Collector Crypt" in name:
        target = MAP["Collector Crypt"]
    elif "FalconX" in name:
        target = MAP["FalconX"]
    elif "edgeX" in name or "edgex" in name.lower():
        target = MAP["edgeX"]
    elif "DePIN" in name:
        target = MAP["DePIN"]
    elif "XRP" in name and ("\u624b\u7eed\u8d39" in name or "fees" in name.lower()):
        target = "https://data.wublock123.com/json/xrp-fees/chart.html"
    elif "GUA" in name or "Superfortune" in name:
        target = MAP["GUA"]
    elif "LAB" in name and "\u73b0\u8d27" in name:
        target = MAP["LAB"]
    elif "ZEC" in name:
        target = MAP["ZEC"]
    elif "\u6ea2\u4ef7" in name or "cb-premium" in name.lower() or "Coinbase \u6bd4\u7279\u5e01\u6ea2\u4ef7" in name:
        target = MAP["Coinbase"]
    elif "RLUSD" in name:
        target = MAP["RLUSD"]
    elif "PropAMM" in name or "Prop AMM" in name:
        target = MAP["PropAMM"]
    elif "\u65e9\u671f\u878d\u8d44" in name or "financing-early" in name.lower():
        target = MAP["\u65e9\u671f\u878d\u8d44"]
    elif "\u534a\u5e74" in name and "ETF" in name:
        target = MAP["ETF \u534a\u5e74"]
    elif "ETF" in name and ("\u6210\u4ea4" in name or "volume" in name.lower()):
        target = MAP["BTC ETF \u65e5\u6210\u4ea4"]
    elif "Puell" in name or "\u7b97\u529b" in name:
        target = MAP["Puell"]
    elif "\u97e9\u56fd" in name and ("CEX" in name or "\u6210\u4ea4" in name):
        target = MAP["\u97e9\u56fd CEX"]
    elif "\u5e01\u5b89\u4eba\u751f" in name:
        target = MAP["\u5e01\u5b89\u4eba\u751f"]
    elif "SpaceX" in name or "SPCX" in name or "spacex" in name.lower():
        target = MAP["SpaceX"]
    elif "STRC" in name or "apxUSD" in name:
        target = MAP["STRC"]
    elif "\u4ee3\u5e01\u5316" in name and ("\u56fd\u503a" in name or "\u50a8\u5907" in name or "treasur" in name.lower()):
        target = MAP["\u4ee3\u5e01\u5316\u56fd\u503a"]
    elif "BNB" in name and "\u73b0\u8d27" in name:
        target = MAP["BNB \u73b0\u8d27"]
    elif "HYPE" in name and "\u73b0\u8d27" in name:
        target = MAP["HYPE \u73b0\u8d27"]
    elif "\u9884\u6d4b\u5e02\u573a" in name and "\u5468" in name:
        target = MAP["\u9884\u6d4b\u5e02\u573a\u5468"]
    elif "Meme" in name and ("\u5e02\u503c" in name or "Robinhood" in name):
        target = "https://data.wublock123.com/dashboard/robinhood-chain"
    elif "Hyperliquid" in name or "HIP-3" in name or "TradeXYZ" in name or "perp" in name.lower():
        if "OI" in name or "\u4efd\u989d" in name or "\u6210\u4ea4" in name or "RWA" in name:
            target = "https://data.wublock123.com/dashboard/hyperliquid"
    elif "\u4ee5\u592a\u574a\u5b63\u5ea6" in name or "eth-quarter" in name.lower():
        target = "https://data.wublock123.com/dashboard/eth-indicators"

    if not target:
        continue
    old = web
    ws2.cell(i, 8).value = f"Lark \u6587\u672b\uff1a{target}\uff08\u4e0d\u518d\u8d34 raw JSON\uff09"
    st = str(ws2.cell(i, 3).value or "")
    if "\u6587\u672b\u4e0d\u518d\u8d34 raw JSON" not in st:
        ws2.cell(i, 3).value = (st.rstrip("\u3002") + f"\u3002{today} \u6587\u672b\u6539\u6302\u770b\u677f/\u770b\u56fe\u3002").lstrip("\u3002")
    updated += 1
    print("row", i, name[:40], "->", target)

# \u65b0\u884c\u603b\u8ff0
r2 = ws2.max_row + 1
ws2.cell(r2, 1).value = "Lark \u6587\u672b\uff1araw JSON \u2192 \u770b\u677f/chart.html"
ws2.cell(r2, 2).value = today
ws2.cell(r2, 3).value = note
ws2.cell(r2, 4).value = "\u4e2d\u6587"
ws2.cell(r2, 5).value = "\u5df2\u90e8\u7f72/\u968f\u4e0b\u6b21 flash"
ws2.cell(r2, 6).value = "\u65b0\u53d1 Lark \u6587\u672b\u4e3a\u770b\u677f\u6216 /json/\u2026/chart.html"
ws2.cell(r2, 7).value = "LARK_ALERT_WEBHOOK"
ws2.cell(r2, 8).value = "\u770b\u677f\u6216 chart.html\uff08\u4e0d\u518d\u8d34 .json\uff09"
print("internal new", r2, "updated rows", updated)
wb2.save(inn)
print("ok")
