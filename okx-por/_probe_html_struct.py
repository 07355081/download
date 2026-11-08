#!/usr/bin/env python3
import re

html = open("/tmp/okx_por.html", encoding="utf-8").read()

# coin cards: look for collateralRatio nearby
for m in re.finditer(r'index_collateralRatio__[^>]+>(\d+(?:\.\d+)?)%', html):
    start = max(0, m.start() - 400)
    end = min(len(html), m.end() + 900)
    chunk = html[start:end]
    print("==== ratio", m.group(1))
    # coin from icon alt/path
    cm = re.search(r'currency/icon/([a-z0-9]+)', chunk, re.I)
    print(" coin?", cm.group(1) if cm else None)
    for label in ["欧易账户资产", "欧易钱包资产", "交易所钱包", "第三方托管钱包"]:
        lm = re.search(
            re.escape(label) + r"</div><div class=\"index_balanceValue__[^\"]+\">([\d,]+(?:\.\d+)?)</div>",
            chunk,
        )
        print(" ", label, lm.group(1) if lm else None)

# dates
for pat in [
    r"\d{4}年\d{1,2}月\d{1,2}日",
    r"20\d{2}-\d{2}-\d{2}",
    r"Sep\s+\d{1,2},\s+2026",
    r"9\s*月\s*8",
    r"data-e2e-okd-select-value=\"(\d+)\"",
]:
    print("datepat", pat, re.findall(pat, html)[:10])

# search other js for api
import urllib.request

js = open("/tmp/okx_por_detail.js", encoding="utf-8").read()
# find fetch/axios urls
for m in re.finditer(r'["\`](/[^"\`]{5,80})["\`]', js):
    u = m.group(1)
    if any(k in u.lower() for k in ("por", "audit", "reserve", "balance", "asset")):
        print("path", u)
