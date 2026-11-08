# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

folder = Path("e:/Code") / "\u5de5\u4f5c\u8bb0\u5f55"
ext = folder / "\u5bf9\u5916\u5de5\u4f5c\u8bb0\u5f55.xlsx"
inn = folder / "\u5bf9\u5185\u5de5\u4f5c\u8bb0\u5f55.xlsx"
note = (
    "2026-10-20 \u6838\u5bf9\u5434\u8bf4 6 \u6708\u6240\u62a5\uff08\u73b0\u8d27 -5.1%\u3001\u5408\u7ea6 +4.2%\u3001"
    "\u6da8\u8dcc\u5e45\u524d\u4e09\uff09\uff1a\u770b\u677f /dashboard/exchange_volume \u5df2\u6709\u73b0\u8d27/\u5408\u7ea6\u6708\u6210\u4ea4\uff1b"
    "Lark \u5df2\u6709\u6708\u73af\u6bd4\u9608\u503c\uff08|%\u226512% notable\uff09\u3002"
    "\u672c\u7bc7 \u00b15%\u7ea7\u4e0d\u8fc7\u7ebf\uff0c\u4e0d\u8865\u63a8\u3002\u4e0d\u6539\u4ee3\u7801\u3002"
)

wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "CEX 6 \u6708\u73b0\u8d27/\u5408\u7ea6\u6708\u6210\u4ea4\u73af\u6bd4\uff08\u5434\u8bf4\u6240\u62a5\u6838\u5bf9\uff09"
ws.cell(r, 2).value = "20261020"
ws.cell(r, 3).value = (
    "\u5df2\u6709\u6570\u636e+\u9608\u503c Lark\uff08exchange_volume\uff09\u3002"
    "\u6b64\u7bc7\u5e45\u5ea6\u672a\u8fc7\u7ebf\uff0c\u4e0d\u8865\u63a8\u3002"
)
ws.cell(r, 4).value = "P2"
ws.cell(r, 5).value = "CEX"
ws.cell(r, 6).value = "cex-asset&vol \u6708\u5ea6\u73b0\u8d27/\u5408\u7ea6 JSON\uff08CoinGecko \u7b49\uff09"
ws.cell(r, 7).value = note
wb.save(ext)
print("external", r)

wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
for i in (12, 14):
    cell = ws2.cell(i, 3)
    old = str(cell.value or "")
    if "6 \u6708\u6240\u62a5" not in old and "6\u6708\u6240\u62a5" not in old:
        cell.value = old.rstrip() + " " + note
        print("internal", i)
wb2.save(inn)
print("ok")
