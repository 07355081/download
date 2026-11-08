#!/usr/bin/env python3
"""Scan flash/monitor scripts for Lark footer links that point at raw JSON."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT.parent / "data-dashboard" / "src" / "app" / "dashboard"
URL_RE = re.compile(r"https://data\.wublock123\.com/[^\s\"'\\]+")

routes = sorted({p.parent.relative_to(DASH).as_posix() for p in DASH.rglob("page.tsx")})
print("dashboard routes:", len(routes))

files = list(ROOT.rglob("flash.py")) + list(ROOT.rglob("monitor.py")) + list(ROOT.rglob("*_flash.py"))
rows = []
for path in files:
    text = path.read_text(encoding="utf-8", errors="replace")
    urls = sorted(set(URL_RE.findall(text)))
    json_urls = [u for u in urls if "/json/" in u and u.rstrip("/").endswith(".json")]
    page_urls = [u for u in urls if "/dashboard/" in u]
    if not json_urls:
        continue
    # does message body append a json link?
    appends = bool(
        re.search(r"\\n\{?(JSON_URL|NEWS_URL|PAGE|JSON_PAGE|VOL_URL|CAT_URL|CEX_URL|HIP3_URL)\}?", text)
        or re.search(r"f?[\"'].*https://data\.wublock123\.com/json/", text)
    )
    rows.append((path.relative_to(ROOT).as_posix(), json_urls, page_urls, appends))

print("modules with json urls:", len(rows))
for rel, ju, pu, appends in rows:
    print("---", rel, "appends=", appends)
    for u in ju:
        print("  JSON", u)
    for u in pu:
        print("  PAGE", u)
