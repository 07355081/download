# -*- coding: utf-8 -*-
"""Fix R2 mistaken overwrite; update real YZi row."""
import openpyxl
from pathlib import Path

inn = Path(r"e:\Code\工作记录\对内工作记录.xlsx")
wb = openpyxl.load_workbook(inn)
ws = wb.active

# clear wrong cols on R2 if they look like YZi status
c3 = str(ws.cell(2, 3).value or "")
if "ENA" in c3 and ("已上线" in c3 or "阈值" in c3):
    for col in range(3, 12):
        ws.cell(2, col).value = None
    print("cleared R2 status cols")

c1 = str(ws.cell(2, 1).value or "")
bit = "2026-10-22 改判接入 YZi Labs ENA→币安免费监控（monitor_yzi_ena）；不补发 10/13。"
old_bad = "已上线。免费 Etherscan/Blockscout"
# ensure changelog has bit
if "monitor_yzi_ena" not in c1:
    old = "2026-10-22 核对 Arkham YZi Labs 转 ENA 进币安：网页无对应，不做 Lark。"
    if old in c1:
        c1 = c1.replace(old, bit)
    else:
        c1 = c1.rstrip() + bit
    ws.cell(2, 1).value = c1

found = None
for r in range(3, ws.max_row + 1):
    name = str(ws.cell(r, 1).value or "")
    if "YZi" in name and "ENA" in name:
        found = r
        break
if found:
    ws.cell(found, 3).value = (
        "已上线。免费 Etherscan/Blockscout；日合计≥500 万 ENA 进币安热钱包发新闻体；"
        "小时任务；bootstrap 不补发 10/13。非 Arkham API。"
    )
    ws.cell(found, 4).value = "阈值"
    ws.cell(found, 5).value = "小时 cron（wallet-watch）"
    ws.cell(found, 6).value = "日汇总一条；as_news；不回扫"
    ws.cell(found, 7).value = "LARK_ALERT_WEBHOOK"
    ws.cell(found, 8).value = "wallet-watch/output/events_yzi_ena.json"
    print("updated", found)
else:
    print("YZi row missing")
wb.save(inn)
print("ok")
