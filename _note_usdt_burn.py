# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

folder = Path("e:/Code") / "\u5de5\u4f5c\u8bb0\u5f55"
inn = folder / "\u5bf9\u5185\u5de5\u4f5c\u8bb0\u5f55.xlsx"
wb = openpyxl.load_workbook(inn)
ws = wb.active
# find header row / last data row for Lark sheet style
r = ws.max_row + 1
# columns: name, date, status, lang, time, when, where, page, source
ws.cell(r, 1).value = "Tether ETH \u9500\u6bc1 + Binance Tron USDT \u6301\u4ed3\uff08CryptoQuant\uff09"
ws.cell(r, 2).value = "2026-10-20"
ws.cell(r, 3).value = (
    "\u65e0\u6cd5\u505a\u540c\u7c7b\u81ea\u52a8\u63a8\u3002"
    "\u770b\u677f\u4ec5\u6709 DefiLlama ethereum_usdt / tron_usdt \u5e02\u503c\uff08/dashboard/stablecoin-marketcap\uff09\uff0c"
    "\u65e0\u5355\u7b14\u9500\u6bc1\u4e8b\u4ef6\u3001\u65e0 Binance Tron USDT \u6301\u4ed3\u3002"
    "burns \u9875\u4e0d\u542b USDT\u3002CryptoQuant \u672a\u63a5\u3002"
)
ws.cell(r, 4).value = "\u4e2d\u6587"
ws.cell(r, 5).value = "\u672a\u6392\u671f"
ws.cell(r, 6).value = (
    "\u4e0d\u63a8\u3002"
    "\u82e5\u53ea\u7528 ETH USDT \u65e5\u6536\u7f29\u4ee3\u66ff\u9500\u6bc1\u4e8b\u4ef6\uff0c"
    "\u4e0e\u7a3f\u4e2d Binance Tron \u6301\u4ed3\u4e0d\u540c\u9898\uff0c\u4e0d\u5192\u5145\u3002"
)
ws.cell(r, 7).value = "\u672a\u63a5"
ws.cell(r, 8).value = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
ws.cell(r, 9).value = (
    "\u8fd1\u90bb\uff1aDefiLlama stablecoins\uff08\u5df2\u6709\uff09\u3002"
    "\u7f3a\uff1aUSDT burn \u4e8b\u4ef6\u3001Binance \u4ea4\u6613\u6240 Tron USDT \u4f59\u989d\uff08CryptoQuant\uff09\u3002"
)
wb.save(inn)
print("internal row", r)
