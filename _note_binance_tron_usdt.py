# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

folder = Path("e:/Code") / "\u5de5\u4f5c\u8bb0\u5f55"
ext = folder / "\u5bf9\u5916\u5de5\u4f5c\u8bb0\u5f55.xlsx"
inn = folder / "\u5bf9\u5185\u5de5\u4f5c\u8bb0\u5f55.xlsx"

# \u5bf9\u5916 = demand log
wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "Binance Tron USDT \u6301\u4ed3\uff08DefiLlama binance-cex\uff09\u2192 Lark"
ws.cell(r, 2).value = "20261020"
ws.cell(r, 3).value = (
    "\u5df2\u4e0a\u7ebf\u3002DefiLlama \u514d\u8d39\uff1b\u6bcf\u65e5\u3010\u5b9e\u65f6\u65e5\u5e38\u62a5\u544a\u3011\uff1b"
    "\u8dcc\u7834 10 \u4ebf / \u8fd1 180 \u65e5\u65b0\u4f4e / \u5355\u65e5|%\u226515 \u8d70\u3010\u5386\u53f2\u9608\u503c\u8b66\u62a5\u3011\u3002"
)
ws.cell(r, 4).value = "P2"
ws.cell(r, 5).value = "CEX / \u7a33\u5b9a\u5e01"
ws.cell(r, 6).value = (
    "DefiLlama api.llama.fi/protocol/binance-cex \u00b7 chainTvls.Tron.tokensInUsd.USDT\u3002"
    "\u4e0d\u662f CryptoQuant\u3002"
)
ws.cell(r, 7).value = (
    "2026-10-20 \u63a5\u5165 binance-tron-usdt\uff1amisc \u65e5\u66f4 + Lark\u3002"
    "JSON /json/binance-tron-usdt/binance_tron_usdt.json\u3002"
    "\u4e0d\u542b Tether ETH \u9500\u6bc1\u4e8b\u4ef6\uff08\u4ecd\u65e0\u5355\u7b14\u9500\u6bc1\u6e90\uff09\u3002"
)
wb.save(ext)
print("external", r)

# \u5bf9\u5185 = Lark sheet\uff1a\u66f4\u65b0\u539f\u300c\u65e0\u6cd5\u505a\u300d\u884c\uff0c\u518d\u8865\u65b0\u884c
wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
updated = False
for i in range(1, ws2.max_row + 1):
    name = str(ws2.cell(i, 1).value or "")
    if "Binance Tron USDT" in name or ("Tether ETH" in name and "Tron USDT" in name):
        if "Binance Tron USDT" in name and "Tether" in name:
            ws2.cell(i, 3).value = (
                "\u90e8\u5206\u4e0a\u7ebf\uff1aBinance Tron USDT \u5df2\u7528 DefiLlama \u63a5\u65e5\u5e38+/\u9608\u503c\u3002"
                "Tether ETH \u5355\u7b14\u9500\u6bc1\u4ecd\u65e0\u6e90\uff0c\u4e0d\u63a8\u3002"
            )
            ws2.cell(i, 5).value = "\u968f misc\uff08stablecoin \u540e\uff09"
            ws2.cell(i, 6).value = (
                "\u6bcf\u65e5\u3010\u5b9e\u65f6\u65e5\u5e38\u62a5\u544a\u3011\uff1b"
                "\u8dcc\u7834 10 \u4ebf/\u8fd1 180 \u65e5\u65b0\u4f4e/\u5355\u65e5|%\u226515 \u53e6\u53d1\u3010\u5386\u53f2\u9608\u503c\u8b66\u62a5\u3011\u3002"
            )
            updated = True
            print("internal updated", i)
            break
if not updated:
    r2 = ws2.max_row + 1
    ws2.cell(r2, 1).value = "Binance Tron USDT \u6301\u4ed3\uff08DefiLlama\uff09"
    ws2.cell(r2, 2).value = "2026-10-20"
    ws2.cell(r2, 3).value = "\u5df2\u4e0a\u7ebf\u3002\u65e5\u5e38+\u9608\u503c\u3002"
    ws2.cell(r2, 4).value = "\u4e2d\u6587"
    ws2.cell(r2, 5).value = "\u968f misc"
    ws2.cell(r2, 6).value = (
        "\u6bcf\u65e5\u4e00\u6761\u65e5\u5e38\uff1b\u8dcc\u7834 10 \u4ebf/\u8fd1 180 \u65e5\u65b0\u4f4e/\u5355\u65e5|%\u226515 \u9608\u503c\u3002"
    )
    ws2.cell(r2, 7).value = "LARK_ALERT_WEBHOOK"
    ws2.cell(r2, 8).value = "/json/binance-tron-usdt/binance_tron_usdt.json"
    print("internal new", r2)
wb2.save(inn)
print("ok")
