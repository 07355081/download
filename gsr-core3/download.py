"""Weekly GSR Core3 weights and performance from public insight pages.

Each post embeds two HTML tables. This keeps those tables and drops the commentary.
The commentary on the same page can disagree with the table, so it is not stored.
The URL slug date can also be wrong; the post title is the week date.
Windows often gets HTTP 403. The production VPS can fetch the pages. No Dune.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "gsr_core3.json"
SITEMAP = "https://www.gsr.io/sitemaps-1-section-insightsDetail-1-sitemap.xml"
UA = "Mozilla/5.0 (compatible; dashboard-data/gsr-core3)"
MIN_WEEKS = 12

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
TITLE_RE = re.compile(
    r"Core3 Model Portfolio\s*[-–]\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})",
    re.I,
)
URL_RE = re.compile(r"https://www\.gsr\.io/insights/core3-model-portfolio-[^<\s\"]+")
ASSET = {
    "core3": "core3",
    "equal weight": "equal_weight",
    "bitcoin": "btc",
    "ether": "eth",
    "solana": "sol",
}
PERF_KEYS = ("ret_1d", "ret_1w", "ret_1m", "ret_ytd", "ret_1y", "vol_30d", "vol_60d")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_pct(text: str) -> float:
    cleaned = text.strip().replace("%", "").replace(",", "")
    return round(float(cleaned), 4)


def title_date(html: str) -> tuple[str, str]:
    match = TITLE_RE.search(html)
    if not match:
        raise ValueError("missing Core3 title date")
    month = MONTHS.get(match.group(1).lower())
    if not month:
        raise ValueError(f"unknown month {match.group(1)}")
    day = datetime(int(match.group(3)), month, int(match.group(2)))
    title = re.sub(r"\s+", " ", match.group(0)).strip()
    return day.date().isoformat(), title


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self._table: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._table = []
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in ("td", "th"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_table:
            if any(self._row):
                self._table.append(self._row)
            self._row = []
        elif tag == "table" and self._in_table:
            if self._table:
                self.tables.append(self._table)
            self._in_table = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def weights_from(tables: list[list[list[str]]]) -> dict[str, float]:
    for table in tables:
        for row in table:
            label = row[0].lower() if row else ""
            if "weight" in label and len(row) >= 4:
                return {"btc": parse_pct(row[1]), "eth": parse_pct(row[2]), "sol": parse_pct(row[3])}
    raise ValueError("weight table missing")


def performance_from(tables: list[list[list[str]]]) -> dict[str, dict[str, float]]:
    for table in tables:
        header = [cell.lower() for cell in table[0]] if table else []
        if "strategy" not in header or "30d vol" not in header:
            continue
        out: dict[str, dict[str, float]] = {}
        for row in table[1:]:
            key = ASSET.get(row[0].strip().lower())
            if not key or len(row) < 8:
                continue
            out[key] = {name: parse_pct(row[i + 1]) for i, name in enumerate(PERF_KEYS)}
        if {"btc", "eth", "sol", "core3"} <= out.keys():
            return out
    raise ValueError("performance table missing")


def parse_page(url: str, html: str) -> dict:
    parser = TableParser()
    parser.feed(html)
    day, title = title_date(html)
    weights = weights_from(parser.tables)
    total = round(sum(weights.values()), 2)
    if not 99 <= total <= 101:
        raise ValueError(f"weights sum to {total}")
    return {
        "date": day,
        "title": title,
        "url": url,
        "weights_pct": weights,
        "performance_pct": performance_from(parser.tables),
    }


def discover_urls() -> list[str]:
    return sorted(set(URL_RE.findall(get(SITEMAP))))


def main() -> int:
    urls = discover_urls()
    if len(urls) < MIN_WEEKS:
        print(f"only {len(urls)} Core3 urls", file=sys.stderr)
        return 1
    by_date: dict[str, dict] = {}
    for url in urls:
        page = parse_page(url, get(url))
        prev = by_date.get(page["date"])
        if prev and prev["weights_pct"] != page["weights_pct"]:
            raise ValueError(f"conflicting weights on {page['date']}")
        if prev is None:
            by_date[page["date"]] = page
        print(f"{page['date']} btc={page['weights_pct']['btc']} eth={page['weights_pct']['eth']} sol={page['weights_pct']['sol']}")
    weeks = [by_date[day] for day in sorted(by_date)]
    if len(weeks) < MIN_WEEKS:
        print(f"only {len(weeks)} weeks", file=sys.stderr)
        return 1
    payload = {
        "code": 0,
        "msg": "success",
        "name": "gsr_core3",
        "description": "GSR Core3 weekly model weights and performance, taken from the HTML tables on the public insight posts.",
        "source": "gsr-public-insights",
        "source_url": "https://www.gsr.io/insights",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_count": len(weeks),
        "weeks": weeks,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    last = weeks[-1]
    print(f"wrote {OUT_PATH} weeks={len(weeks)} latest={last['date']} btc={last['weights_pct']['btc']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"gsr-core3 failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
