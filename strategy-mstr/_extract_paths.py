"""Extract api.strategy.com paths from cached JS."""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"

pat = re.compile(
    r'https://api\.strategy\.com["\']?\s*,\s*["\'](/[^"\']+)["\']'
    r'|https://api\.strategy\.com(/[^"\'\s]+)'
    r'|concat\(["\']https://api\.strategy\.com["\'],["\'](/[^"\']+)["\']\)'
)

paths = set()
for f in CACHE.glob("js_*"):
    text = f.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'api\.strategy\.com["\']?\s*,\s*["\'](/[^"\']+)["\']', text):
        paths.add(m.group(1))
    for m in re.finditer(r'https://api\.strategy\.com(/[a-zA-Z0-9_./?-]+)', text):
        paths.add(m.group(1))
    for m in re.finditer(r'["\']/(btc/[^"\']+)["\']', text):
        paths.add("/" + m.group(1) if not m.group(1).startswith("btc") else "/" + m.group(1))

print("paths:")
for p in sorted(paths):
    print(" ", p)

# also dump nearby context for mstrKpi
for f in CACHE.glob("js_*"):
    text = f.read_text(encoding="utf-8", errors="replace")
    if "mstrKpi" in text or "bitcoinHistory" in text or "kpiHistory" in text or "download" in text.lower():
        for key in ("mstrKpi", "bitcoinHistory", "kpiHistory", "metricData", "historyKpi", "/data"):
            i = text.find(key)
            if i >= 0:
                print(f"\n{f.name} :: {key}")
                print(text[max(0, i - 80) : i + 160].replace("\n", " ")[:300])
