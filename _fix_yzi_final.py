# -*- coding: utf-8 -*-
import openpyxl
from pathlib import Path

inn = Path(r"e:\Code\工作记录\对内工作记录.xlsx")
wb = openpyxl.load_workbook(inn)
ws = wb.active

# R2 must stay changelog-only
for col in range(3, 12):
    val = str(ws.cell(2, col).value or "")
    if "ENA" in val or "YZi" in val or "已上线" in val:
        ws.cell(2, col).value = None

c1 = str(ws.cell(2, 1).value or "")
bit = "2026-10-22 改判接入 YZi Labs ENA 大额转出免费监控（monitor_yzi_ena，RPC）；不补发 10/13。"
if "monitor_yzi_ena" not in c1:
    c1 = c1.rstrip() + bit
    ws.cell(2, 1).value = c1
elif "RPC" not in c1 and "monitor_yzi_ena" in c1:
    ws.cell(2, 1).value = c1.replace(
        "monitor_yzi_ena）；不补发 10/13。",
        "monitor_yzi_ena，RPC）；不补发 10/13。",
    )

for r in range(3, ws.max_row + 1):
    name = str(ws.cell(r, 1).value or "")
    if name.startswith("YZi Labs") and "ENA" in name:
        ws.cell(r, 3).value = (
            "已上线。公共 RPC eth_getLogs + 回退浏览器；日合计≥500 万 ENA 转出发新闻体；"
            "命中公开币安热钱包则标明；小时 cron；bootstrap 不补发 10/13。非 Arkham API。"
        )
        ws.cell(r, 4).value = "阈值"
        ws.cell(r, 5).value = "小时 cron（wallet-watch）"
        ws.cell(r, 6).value = "日汇总一条；as_news；不回扫"
        ws.cell(r, 7).value = "LARK_ALERT_WEBHOOK"
        ws.cell(r, 8).value = "wallet-watch/output/events_yzi_ena.json"
        print("updated", r)
        break

# 对外：更新最近一行若标题匹配
ext = Path(r"e:\Code\工作记录\对外工作记录.xlsx")
wb2 = openpyxl.load_workbook(ext)
ws2 = wb2.active
for r in range(ws2.max_row, max(1, ws2.max_row - 15), -1):
    if "YZi" in str(ws2.cell(r, 1).value or "") and "ENA" in str(ws2.cell(r, 1).value or ""):
        ws2.cell(r, 3).value = (
            "已上线。公共 RPC 监控 0x877b…4bba6 的 ENA 大额转出；日合计≥500 万枚发新闻体；"
            "命中公开币安热钱包则标明；小时 cron；bootstrap 不补发 10/13。"
        )
        ws2.cell(r, 6).value = "wallet-watch/monitor_yzi_ena.py（RPC）；非 Arkham API"
        print("external updated", r)
        break
wb.save(inn)
wb2.save(ext)
print("ok")
