#!/usr/bin/env python3
"""扫描 flash/monitor 文末链接：raw JSON / 第三方 / 无链接。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP = {"_fix", "_scan", "_note", "_tmp", "_deploy", "_patch", "__pycache__"}

# 文末常见写法
FOOTER_ASSIGN = re.compile(
    r"^(?P<var>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|CHART_URL|NEWS_URL|CG_PAGE|SOURCE_URL)\s*=\s*[\"'](?P<url>[^\"']+)[\"']",
    re.M,
)
FOOTER_USE = re.compile(
    r"""(?:\\n|\\\\n|\n)\{(?P<var>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|CHART_URL|NEWS_URL)\}"""
    r"""|(?:f["'].*\{(?P<var2>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|CHART_URL|NEWS_URL)\})"""
    r"""|(?:append\(\s*(?P<var3>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|CHART_URL|NEWS_URL)\s*\))"""
    r"""|(?:bits\.append\(f?["'].*\{(?P<var4>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|CHART_URL|NEWS_URL)\})""",
)

OWN = ("data.wublock123.com", "data.wublockchain.xyz", "wublock123.com")
THIRD = (
    "defillama.com",
    "coingecko.com",
    "coinglass.com",
    "dune.com",
    "arkham",
    "tokenterminal",
    "cryptorank",
    "yahoo.com",
    "binance.com",
)


def classify(url: str) -> str:
    u = url.lower()
    if u.endswith(".json") or "/json/" in u and u.endswith(".json"):
        return "json"
    if "chart.html" in u:
        return "chart"
    if "/dashboard/" in u and any(o in u for o in OWN):
        return "dashboard"
    if any(o in u for o in OWN):
        return "own_other"
    if any(t in u for t in THIRD):
        return "third"
    if u.startswith("http"):
        return "other_http"
    return "unknown"


def main() -> None:
    rows: list[tuple[str, str, str, str]] = []
    for p in sorted(ROOT.rglob("*.py")):
        if any(s in p.parts for s in SKIP) or p.name.startswith("_"):
            continue
        if not (
            "flash" in p.name
            or p.name == "monitor.py"
            or p.name.endswith("_flash.py")
        ):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "as_news" not in text and "webhook" not in text.lower() and "LARK" not in text:
            continue
        assigns = {m.group("var"): m.group("url") for m in FOOTER_ASSIGN.finditer(text)}
        used: set[str] = set()
        for m in FOOTER_USE.finditer(text):
            for k in ("var", "var2", "var3", "var4"):
                if m.group(k):
                    used.add(m.group(k))
        # also plain f"\n{PAGE}" style already covered; check literal https in f-string near end builders
        lit = re.findall(
            r"""f["'][^"']*https?://[^"'\s]+|["']\\nhttps?://[^"']+["']""",
            text,
        )
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if not used and not assigns:
            # might still append nothing
            if "as_news" in text:
                rows.append((rel, "(no PAGE/JSON const)", "-", "maybe_none"))
            continue
        for var in sorted(used or assigns):
            url = assigns.get(var, "?")
            kind = classify(url) if url != "?" else "missing"
            if var not in used and used:
                continue  # assigned but not in message
            rows.append((rel, var, url, kind))

    bad = [r for r in rows if r[3] in ("json", "third", "other_http", "maybe_none", "missing")]
    ok = [r for r in rows if r[3] in ("dashboard", "chart", "own_other")]
    print(f"ok={len(ok)} bad={len(bad)} total_footer_refs={len(rows)}")
    print("--- BAD ---")
    for r in bad:
        print(f"{r[3]:12} {r[0]:45} {r[1]:16} {r[2]}")
    print("--- OK sample ---")
    for r in ok[:15]:
        print(f"{r[3]:12} {r[0]:45} {r[2]}")


if __name__ == "__main__":
    main()
