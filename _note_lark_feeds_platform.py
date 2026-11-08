# -*- coding: utf-8 -*-
from pathlib import Path
import openpyxl

folder = Path("e:/Code") / "工作记录"
ext = folder / "对外工作记录.xlsx"
inn = folder / "对内工作记录.xlsx"
today = "2026-10-22"
note = (
    "2026-10-22 Lark 文末统一挂数据平台："
    "新建 /dashboard/lark-feeds 目录（侧栏可点），明细 /dashboard/lark-feeds/{slug} 带图；"
    "禁止文末挂 DefiLlama/Dune/交易所官网等第三方；"
    "链上监控文末只留本站链接+交易哈希，不挂浏览器站。"
)

wb = openpyxl.load_workbook(ext)
ws = wb.active
r = ws.max_row + 1
ws.cell(r, 1).value = "Lark 推送文末：平台目录+图（禁第三方）"
ws.cell(r, 2).value = today.replace("-", "")
ws.cell(r, 3).value = "已完成。侧栏「Lark 数据图」可点进各明细页。"
ws.cell(r, 4).value = "P1"
ws.cell(r, 5).value = "数据平台 / Lark"
ws.cell(r, 6).value = "data-dashboard lark-feeds + data-download flash 文末"
ws.cell(r, 7).value = note
wb.save(ext)
print("external", r)

wb2 = openpyxl.load_workbook(inn)
ws2 = wb2.active
rules = str(ws2.cell(2, 1).value or "")
if "Lark 数据图" not in rules:
    ws2.cell(2, 1).value = (rules.rstrip() + " " + note).strip()
r2 = ws2.max_row + 1
ws2.cell(r2, 1).value = "Lark 文末：平台目录 /dashboard/lark-feeds"
ws2.cell(r2, 2).value = today
ws2.cell(r2, 3).value = note
ws2.cell(r2, 4).value = "中文"
ws2.cell(r2, 5).value = "随发版 / flash"
ws2.cell(r2, 6).value = "每条推送文末必须是本站看板或 lark-feeds 明细（含图）"
ws2.cell(r2, 7).value = "LARK_ALERT_WEBHOOK"
ws2.cell(r2, 8).value = "https://data.wublock123.com/dashboard/lark-feeds"
wb2.save(inn)
print("internal", r2)
