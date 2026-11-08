# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

folder = Path("e:/Code") / "工作记录"
ext = folder / "对外工作记录.xlsx"
inn = folder / "对内工作记录.xlsx"

wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "YZi Labs 地址向币安转入 ENA（Arkham 稿）→ Lark"
ws.cell(r, 2).value = "20261022"
ws.cell(r, 3).value = (
    "已上线。Etherscan/Blockscout 免费监控 0x877b…4bba6 的 ENA 大额转出；"
    "上海日合计≥500 万枚发新闻体；命中公开币安热钱包则标明；小时 cron；bootstrap 不补发 10/13。"
)
ws.cell(r, 4).value = "P2"
ws.cell(r, 5).value = "链上 / 地址"
ws.cell(r, 6).value = "wallet-watch/monitor_yzi_ena.py；非 Arkham API"
ws.cell(r, 7).value = "2026-10-22 接入；实体标签仅参考 Arkham。"
wb.save(ext)
print("external", r)

wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
updated = False
for i in range(1, ws2.max_row + 1):
    name = str(ws2.cell(i, 1).value or "")
    if "YZi" in name and "ENA" in name:
        ws2.cell(i, 3).value = (
            "已上线。免费 Etherscan/Blockscout；日合计≥500 万 ENA 进币安热钱包发新闻体；"
            "小时任务；bootstrap 不补发 10/13。非 Arkham API。"
        )
        ws2.cell(i, 4).value = "阈值"
        ws2.cell(i, 5).value = "小时 cron（wallet-watch）"
        ws2.cell(i, 6).value = "日汇总一条；as_news；不回扫"
        ws2.cell(i, 7).value = "LARK_ALERT_WEBHOOK"
        ws2.cell(i, 8).value = "wallet-watch/output/events_yzi_ena.json"
        updated = True
        print("internal updated", i)
        break
if not updated:
    r2 = ws2.max_row + 1
    ws2.cell(r2, 1).value = "YZi Labs 地址向币安转入 ENA（Arkham）"
    ws2.cell(r2, 2).value = "2026-10-22"
    ws2.cell(r2, 3).value = "已上线。免费链上监控；日合计阈值；不补发历史。"
    ws2.cell(r2, 4).value = "阈值"
    ws2.cell(r2, 5).value = "小时 cron"
    ws2.cell(r2, 6).value = "as_news"
    ws2.cell(r2, 7).value = "LARK_ALERT_WEBHOOK"
    print("internal new", r2)

c1 = str(ws2.cell(2, 1).value or "")
bit = "2026-10-22 改判接入 YZi Labs ENA→币安免费监控（monitor_yzi_ena）；不补发 10/13。"
if "monitor_yzi_ena" not in c1:
    # replace old 不做 note if present
    old = "2026-10-22 核对 Arkham YZi Labs 转 ENA 进币安：网页无对应，不做 Lark。"
    if old in c1:
        c1 = c1.replace(old, bit)
    else:
        c1 = c1.rstrip() + bit
    ws2.cell(2, 1).value = c1
wb2.save(inn)
print("ok")
