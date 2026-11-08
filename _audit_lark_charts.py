# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path("/root/data-download")
DASH = Path("/root/dashboard/public/json")
TODAY = "2026-10-29"
FEEDS_TS = Path("/tmp/lark-feeds.ts")


def load_feeds() -> dict[str, str]:
    feeds: dict[str, str] = {}
    for line in FEEDS_TS.read_text(encoding="utf-8").splitlines():
        m = re.search(r'slug:\s*"([^"]+)".*jsonPath:\s*"([^"]+)"', line)
        if m:
            feeds[m.group(1)] = m.group(2)
    return feeds


def chartable(doc: object) -> bool:
    if not isinstance(doc, dict):
        return False
    for key in (
        "series",
        "data",
        "history",
        "returns",
        "top",
        "latest_top",
        "leaders",
        "months",
        "quarters",
        "days",
        "points",
    ):
        val = doc.get(key)
        if isinstance(val, list) and val:
            return True
    if isinstance(doc.get("latest"), dict) and doc["latest"]:
        return True
    if isinstance(doc.get("latest_pack"), dict) and doc["latest_pack"]:
        return True
    return False


def module_pages(state: Path) -> list[str]:
    mod = state.parent.parent if state.parent.name == "output" else state.parent
    pages: set[str] = set()
    for flash in list(mod.glob("*flash*.py")) + list(mod.glob("monitor.py")):
        try:
            src = flash.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.findall(r"https://data\.wublock123\.com/dashboard/[^\s\"']+", src):
            pages.add(m.rstrip(".,)"))
    return sorted(pages)


def main() -> int:
    feeds = load_feeds()
    missing = []
    empty = []
    for slug, jp in sorted(feeds.items()):
        path = DASH / jp
        if not path.is_file():
            missing.append((slug, jp))
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            empty.append((slug, jp, f"bad-json:{exc}"))
            continue
        if not chartable(doc):
            empty.append((slug, jp, "no-fields"))

    print(f"feeds={len(feeds)}")
    print("MISSING JSON:")
    for row in missing:
        print(" ", row)
    print("EMPTY / NO CHART FIELDS:")
    for row in empty:
        print(" ", row)

    print("\nTODAY REAL SENDS:")
    for path in sorted(ROOT.rglob("*lark*.json")):
        if any(x in str(path) for x in ("node_modules", ".venv")):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        ids: list[str] = []
        for key in ("sentIds", "sent", "ids"):
            val = doc.get(key)
            if isinstance(val, list):
                ids.extend(
                    str(x)
                    for x in val
                    if TODAY in str(x) or TODAY.replace("-", "") in str(x)
                )
        if not ids:
            continue
        pages = module_pages(path)
        print(f"{path.relative_to(ROOT)} | ids={ids[:4]} | pages={pages[:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
