# -*- coding: utf-8 -*-
import urllib.request

u = "https://data.wublock123.com/_next/static/chunks/app/dashboard/lark-feeds/%5Bslug%5D/page-fbadd80cacebb647.js"
req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
print("len", len(text))
for k in ("打开数据文件", "JSON.stringify", "leaders", "max-h-64", "未识别到可画图字段"):
    print(k, text.find(k))
i = text.find("max-h-64")
print("---")
print(text[max(0, i - 200): i + 400] if i >= 0 else "no pre")
j = text.find("打开数据文件")
print("--- link ---")
print(text[max(0, j - 250): j + 80] if j >= 0 else "no link")
