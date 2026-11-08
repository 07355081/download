# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

folder = Path("e:/Code") / "\u5de5\u4f5c\u8bb0\u5f55"
ext = folder / "\u5bf9\u5916\u5de5\u4f5c\u8bb0\u5f55.xlsx"
inn = folder / "\u5bf9\u5185\u5de5\u4f5c\u8bb0\u5f55.xlsx"

wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "Aave v4 \u5b58\u6b3e/\u501f\u6b3e + Spoke\uff08JackMandin \u7a3f\uff0c\u514d\u8d39\uff09\u2192 Lark"
ws.cell(r, 2).value = "20261021"
ws.cell(r, 3).value = (
    "\u5df2\u4e0a\u7ebf\u3002Aave GraphQL \u5b58\u6b3e ATH \u6216\u8fd1 7 \u65e5|%\u226515 \u53d1\u65b0\u95fb\u4f53\uff1b"
    "\u9996\u65e5\u5efa\u57fa\u7ebf\u4e0d\u63a8\uff1b\u6b63\u6587\u542b\u501f\u6b3e\u3001Eth \u5360\u6bd4\u3001Bluechip/EtherFi/\u5916\u6c47\u9ec4\u91d1 Spoke\u3002"
    "\u975e JackMandin\uff1b\u5916\u7535 2 \u4ebf/6000 \u4e07\u4e0d\u5bf9\u9f50\u3002"
)
ws.cell(r, 4).value = "P2"
ws.cell(r, 5).value = "DeFi"
ws.cell(r, 6).value = (
    "api.v4.aave.com/graphql + DefiLlama aave-v4\u3002\u968f misc\u3002"
)
ws.cell(r, 7).value = (
    "2026-10-21 \u63a5\u5165 aave-v4-spokes\uff1adownload+flash\uff1b"
    "JSON /json/aave-v4-spokes/aave_v4_spokes.json\u3002"
)
wb.save(ext)
print("external", r)

wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
updated = False
for i in range(1, ws2.max_row + 1):
    name = str(ws2.cell(i, 1).value or "")
    if "Aave v4" in name and ("Spoke" in name or "\u5b58\u6b3e" in name):
        ws2.cell(i, 3).value = (
            "\u5df2\u4e0a\u7ebf\u3002\u514d\u8d39 GraphQL+Llama\uff1b"
            "\u5b58\u6b3e ATH/\u8fd1 7 \u65e5|%\u226515 \u65b0\u95fb\u4f53\uff1b\u9996\u65e5\u5efa\u7ebf\u4e0d\u63a8\uff1b\u4e0d\u56de\u626b\u3002"
            "\u6b63\u6587\u542b\u501f\u6b3e\u3001Eth\u3001Bluechip/EtherFi/\u5916\u6c47\u9ec4\u91d1 Spoke\u3002"
        )
        ws2.cell(i, 4).value = "\u9608\u503c"
        ws2.cell(i, 5).value = "\u968f misc\uff08aave-llama \u540e\uff09"
        ws2.cell(i, 6).value = (
            "\u4ec5 ATH/\u8fd1 7 \u65e5\u663e\u8457\u53d8\u5316\uff1bas_news\uff1b\u540c\u65e5\u4e00\u6b21\uff1b\u4e0d\u56de\u626b"
        )
        ws2.cell(i, 7).value = "LARK_ALERT_WEBHOOK"
        ws2.cell(i, 8).value = "/json/aave-v4-spokes/aave_v4_spokes.json"
        note = (
            " 2026-10-21 \u6539\u4e3a\u514d\u8d39\u63a5\u5165 aave-v4-spokes\uff08GraphQL \u5b58\u6b3e/Spoke + Llama\uff09\uff1b"
            "\u9996\u65e5\u5efa\u7ebf\u4e0d\u63a8\u3002"
        )
        prev = ws2.cell(i, 9).value or ws2.cell(i, 10).value or ""
        # find a free notes col - try col 9 then append to col 3 style notes in last used
        for col in (9, 10, 11):
            cell = ws2.cell(i, col)
            if cell.value is None or "aave-v4-spokes" not in str(cell.value):
                if cell.value and "2026-10-21" in str(cell.value) and "Spoke" in str(cell.value):
                    cell.value = str(cell.value) + note
                elif cell.value:
                    cell.value = str(cell.value) + note
                else:
                    cell.value = note.strip()
                break
        updated = True
        print("internal updated", i)
        break
if not updated:
    r2 = ws2.max_row + 1
    ws2.cell(r2, 1).value = "Aave v4 \u5b58\u6b3e\u65b0\u9ad8\u4e0e Spoke \u7ed3\u6784\uff08JackMandin\uff09"
    ws2.cell(r2, 2).value = "2026-10-21"
    ws2.cell(r2, 3).value = "\u5df2\u4e0a\u7ebf\u3002\u9608\u503c\u65b0\u95fb\u4f53\uff1b\u9996\u65e5\u5efa\u7ebf\u4e0d\u63a8\u3002"
    ws2.cell(r2, 4).value = "\u9608\u503c"
    ws2.cell(r2, 5).value = "\u968f misc"
    ws2.cell(r2, 6).value = "ATH/\u8fd1 7 \u65e5|%\u226515\uff1bas_news"
    ws2.cell(r2, 7).value = "LARK_ALERT_WEBHOOK"
    ws2.cell(r2, 8).value = "/json/aave-v4-spokes/aave_v4_spokes.json"
    print("internal new", r2)
wb2.save(inn)
print("ok")
