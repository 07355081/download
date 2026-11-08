# -*- coding: utf-8 -*-
"""Scan today's Lark sends and check linked lark-feeds JSON / chartability."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/data-download")
DASH = Path("/root/dashboard/public/json")
TODAY = "2026-10-29"
TZ = timezone(timedelta(hours=8))
FEEDS_TS = Path("/tmp/lark-feeds.ts")


def load_feeds() -> dict[str, str]:
    src = FEEDS_TS.read_text(encoding="utf-8")
    feeds: dict[str, str] = {}
    for line in src.splitlines():
        if "slug:" not in line or "jsonPath:" not in line:
            continue
        m = re.search(r'slug:\s*"([^"]+)".*jsonPath:\s*"([^"]+)"', line)
        if m:
            feeds[m.group(1)] = m.group(2)
    return feeds


def pages_for_state(state: Path) -> list[str]:
    pages: list[str] = []
    for folder in {state.parent, state.parent.parent}:
        if not folder.is_dir():
            continue
        for flash in folder.glob("*.py"):
            if "flash" not in flash.name and flash.name not in {"monitor.py"}:
                continue
            try:
                src = flash.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.findall(r"https://data\.wublock123\.com/dashboard/[^\s\"']+", src):
                pages.append(m.rstrip(".,)"))
    return sorted(set(pages))


def chartable(doc: object) -> tuple[bool, str]:
    if not isinstance(doc, dict):
        return False, "not-object"
    if isinstance(doc.get("series"), list) and doc["series"]:
        return True, "series"
    if isinstance(doc.get("data"), list) and doc["data"]:
        return True, "data"
    if isinstance(doc.get("history"), list) and doc["history"]:
        return True, "history"
    if isinstance(doc.get("returns"), list) and doc["returns"]:
        return True, "returns"
    if isinstance(doc.get("top"), list) and doc["top"]:
        return True, "top"
    if isinstance(doc.get("latest_top"), list) and doc["latest_top"]:
        return True, "latest_top"
    if isinstance(doc.get("latest"), dict) and doc["latest"]:
        return True, "latest"
    if isinstance(doc.get("latest_pack"), dict) and doc["latest_pack"]:
        return True, "latest_pack"
    if any(k in doc for k in ("tvl_usd", "volume_usd", "market_cap_usd", "oi_usd")):
        return True, "scalar"
    # nested common shapes
    for key in ("months", "quarters", "days", "points"):
        if isinstance(doc.get(key), list) and doc[key]:
            return True, key
    return False, "no-chart-fields"


def main() -> int:
    feeds = load_feeds()
    print(f"feeds={len(feeds)}")
    states = []
    for p in ROOT.rglob("*lark*.json"):
        if any(x in str(p) for x in ("node_modules", ".venv")):
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, TZ).strftime("%Y-%m-%d")
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = json.dumps(doc, ensure_ascii=False)
        if mtime != TODAY and TODAY not in text:
            continue
        ids: list[str] = []
        if isinstance(doc, dict):
            for key in ("sentIds", "sent", "ids"):
                v = doc.get(key)
                if isinstance(v, list):
                    ids.extend(
                        str(x)
                        for x in v
                        if TODAY in str(x) or TODAY.replace("-", "") in str(x)
                    )
            ua = str(doc.get("updatedAt") or doc.get("updated_at") or "")
            if TODAY in ua and not ids:
                ids = ["(updatedAt today)"]
        if not ids and mtime == TODAY:
            ids = ["(mtime today)"]
        if not ids:
            continue
        # Prefer real send ids for "actually pushed today"
        real = [i for i in ids if not i.startswith("(")]
        states.append(
            {
                "state": str(p.relative_to(ROOT)),
                "mtime": mtime,
                "ids": (real or ids)[:8],
                "pushed": bool(real),
                "pages": pages_for_state(p),
            }
        )

    missing = []
    empty = []
    ok = []
    non = []
    seen_slug = set()
    for s in states:
        # only focus on ones that look like they actually sent an id today
        if not s["pushed"]:
            continue
        lark = [u for u in s["pages"] if "/lark-feeds/" in u]
        if not lark:
            non.append(s)
            continue
        for url in sorted(set(lark)):
            slug = url.rstrip("/").split("/")[-1]
            key = (slug, s["state"])
            if key in seen_slug:
                continue
            seen_slug.add(key)
            jp = feeds.get(slug)
            if not jp:
                missing.append(
                    {
                        "status": "no-feed-config",
                        "slug": slug,
                        "url": url,
                        "jsonPath": None,
                        "size": 0,
                        "state": s["state"],
                        "ids": s["ids"][:3],
                    }
                )
                continue
            path = DASH / jp
            if not path.is_file():
                missing.append(
                    {
                        "status": "missing-json",
                        "slug": slug,
                        "url": url,
                        "jsonPath": jp,
                        "size": 0,
                        "state": s["state"],
                        "ids": s["ids"][:3],
                    }
                )
                continue
            size = path.stat().st_size
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                missing.append(
                    {
                        "status": "bad-json",
                        "slug": slug,
                        "url": url,
                        "jsonPath": jp,
                        "size": size,
                        "state": s["state"],
                        "ids": s["ids"][:3],
                    }
                )
                continue
            good, reason = chartable(payload)
            row = {
                "status": "ok" if good else "no-chart-fields",
                "slug": slug,
                "url": url,
                "jsonPath": jp,
                "size": size,
                "reason": reason,
                "state": s["state"],
                "ids": s["ids"][:3],
            }
            if good:
                ok.append(row)
            else:
                empty.append(row)

    print(f"pushed_states={sum(1 for s in states if s['pushed'])} ok={len(ok)} missing={len(missing)} empty={len(empty)} non_lark={len(non)}")
    print("\n=== MISSING JSON / NO FEED ===")
    for r in missing:
        print(f"{r['status']}\t{r['slug']}\t{r['jsonPath']}\tfrom={r['state']}\tids={r['ids']}")
    print("\n=== JSON EXISTS BUT NO CHART FIELDS ===")
    for r in empty:
        print(f"{r['status']}\t{r['slug']}\t{r['jsonPath']}\treason={r['reason']}\tfrom={r['state']}\tids={r['ids']}")
    print("\n=== OK SAMPLE ===")
    for r in ok[:20]:
        print(f"ok\t{r['slug']}\t{r['reason']}\tids={r['ids']}")
    print("\n=== NON LARK-FEED PUSHED ===")
    for s in non:
        pages = [u for u in s["pages"] if "wublock123.com" in u][:2]
        print(f"{s['state']}\t{pages}\t{s['ids'][:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
