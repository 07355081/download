import openpyxl
from pathlib import Path

root = Path("e:/Code")
folder = next(
    p
    for p in root.iterdir()
    if p.is_dir() and len(list(p.glob("*.xlsx"))) >= 2
)
books = {p.name: p for p in folder.glob("*.xlsx")}
# pick by sheet/header instead of filename
work = lark = None
for path in books.values():
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    head = str(ws.cell(1, 1).value or "")
    if head.startswith("\u9700\u6c42"):
        work = (path, wb, ws)
    elif "Lark" in head or head.startswith("Lark"):
        lark = (path, wb, ws)
    else:
        wb.close()

note = (
    "2026-10-16 \u5b9e\u6d4b\u514d\u8d39\u516c\u5f00\u63a5\u53e3\uff1a"
    "Upbit\u3001Bithumb \u65e5K \u7684 candle_acc_trade_price \u4e3a\u97e9\u5143\u6210\u4ea4\u989d\uff0c"
    "\u53ef\u6309\u5e02\u573a\u52a0\u603b\u56de\u6eaf\uff1bCoinone \u65e5K \u6709 quote_volume\uff0c\u4e00\u6b21\u7ea6 200 \u6839\u3002"
    "Korbit\u3001GOPAX \u516c\u5f00\u63a5\u53e3\u53ea\u6709 24 \u5c0f\u65f6\u57fa\u7840\u5e01\u6210\u4ea4\u91cf\uff0c"
    "\u6ca1\u6709\u97e9\u5143\u5386\u53f2\u65e5K\uff0c\u5386\u53f2\u5468\u4efd\u989d\u8865\u4e0d\u9f50\u3002"
    "\u65e5\u5207\u65f6\u533a\u4e09\u5bb6\u4e0d\u4e00\u81f4\uff0c\u548c\u5916\u7535\u90a3\u4e00\u5468\u4e0d\u4f1a\u9010\u5b57\u5bf9\u9f50\u3002"
)
path, wb, ws = work
cell = ws.cell(150, 7)
old = str(cell.value or "")
if "Korbit" not in old or "GOPAX" not in old:
    cell.value = old.rstrip() + note
ws.cell(150, 3).value = (
    "\u514d\u8d39\u6e20\u9053\u8bd5\u8fc7\u3002Upbit/Bithumb/Coinone \u53ef\u81ea\u5efa\u97e9\u5143\u65e5\u6210\u4ea4\uff1b"
    "Korbit \u4e0e GOPAX \u65e0\u5386\u53f2\u97e9\u5143\u65e5K\u3002\u672a\u63a5\u81ea\u52a8\u63a8\u9001\u3002"
)
wb.save(path)
path2, wb2, ws2 = lark
c = ws2.cell(97, 3)
old2 = str(c.value or "")
if "Korbit" not in old2:
    c.value = old2.rstrip() + " 2026-10-16\uff1a\u4e09\u6240\u53ef\u56de\u6eaf\uff0cKorbit/GOPAX \u53ea\u6709 24h\uff0c\u5148\u4e0d\u63a5\u3002"
wb2.save(path2)
print("ok", path.name, path2.name)
