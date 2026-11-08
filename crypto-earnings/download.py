#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美国上市、在 SEC 申报的加密相关公司：最新业绩 8-K/6-K。

免费 data.sec.gov。不占 Dune。不是全球所有加密公司。
营收/稀释 EPS 来自 XBRL 最近一季，与新闻稿分部口径可能不一致。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "crypto_earnings.json"
UA = "WuBlockchain data-dashboard research@wublock123.com"
TIMEOUT = 40
SLEEP = 0.15

# 主业或财报里加密是主要披露项的美股。不含未上市、不含只在港股/其他市场披露的公司。
COMPANIES: list[tuple[str, str]] = [
    ("HOOD", "Robinhood"),
    ("COIN", "Coinbase"),
    ("CRCL", "Circle"),
    ("MSTR", "Strategy"),
    ("BMNR", "BitMine"),
    ("MARA", "Mara"),
    ("RIOT", "Riot Platforms"),
    ("CLSK", "CleanSpark"),
    ("CIFR", "Cipher Mining"),
    ("WULF", "TeraWulf"),
    ("HUT", "Hut 8"),
    ("BTDR", "Bitdeer"),
    ("CORZ", "Core Scientific"),
    ("IREN", "IREN"),
    ("BITF", "Bitfarms"),
    ("HIVE", "Hive Digital"),
    ("CAN", "Canaan"),
    ("GLXY", "Galaxy Digital"),
    ("SBET", "SharpLink"),
    ("ASST", "Strive"),
]

REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
)
CRYPTO_KEYS = ("crypto", "bitcoin", "digital asset", "stablecoin")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/html"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET {url} failed: {last}")


def http_json(url: str) -> Any:
    return json.loads(http_get(url).decode("utf-8", errors="replace"))


def pause() -> None:
    time.sleep(SLEEP)


def load_ciks() -> dict[str, str]:
    doc = http_json("https://www.sec.gov/files/company_tickers.json")
    pause()
    out: dict[str, str] = {}
    for row in doc.values():
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if ticker and isinstance(cik, int):
            out[ticker] = str(cik).zfill(10)
    return out


def html_text(raw: str) -> str:
    class Collector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("script", "style"):
                self.skip += 1
            if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3"):
                self.parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style") and self.skip:
                self.skip -= 1
            if tag in ("p", "div", "tr", "li"):
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self.skip:
                self.parts.append(data)

    parser = Collector()
    parser.feed(raw)
    text = " ".join(part.strip() for part in parser.parts if part.strip())
    text = (
        text.replace("&#160;", " ")
        .replace("&nbsp;", " ")
        .replace("&#8211;", "-")
        .replace("&amp;", "&")
    )
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("revenue s", "revenues").replace("y ear", "year")
    return text


def lead_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+|[•▪]", text)
    scored: list[tuple[int, str]] = []
    for part in parts:
        piece = part.strip()
        low = piece.lower()
        if "$" not in piece or not (40 <= len(piece) <= 420):
            continue
        score = 0
        if "total net revenue" in low or "total revenue" in low:
            score += 6
        if "revenue" in low:
            score += 3
        if "diluted" in low or "earnings per share" in low:
            score += 2
        if any(key in low for key in CRYPTO_KEYS):
            score += 1
        if piece.count("$") > 2 or "Y/Y" in piece or "......" in piece:
            score = 0
        if score:
            scored.append((score, piece))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    if not scored:
        return ""
    piece = scored[0][1]
    return piece.lstrip("•▪·- ").strip()


def exhibit_rank(name: str) -> int:
    low = name.lower()
    if "index" in low or low.startswith("r1."):
        return -100
    score = 0
    if "press" in low or "release" in low:
        score += 10
    if re.search(r"ex99|exhibit99|991", low):
        score += 6
    if any(token in low for token in ("deck", "overview", "historic", "slide", "presentation")):
        score -= 8
    return score


def pick_exhibit(cik: str, accession: str) -> str:
    nodash = accession.replace("-", "")
    cik_int = str(int(cik))
    index = http_json(f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/index.json")
    pause()
    names = [
        str(item.get("name") or "")
        for item in index.get("directory", {}).get("item") or []
        if str(item.get("name") or "").lower().endswith((".htm", ".html"))
    ]
    if not names:
        return ""
    chosen = max(names, key=exhibit_rank)
    if exhibit_rank(chosen) < 0:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/{chosen}"


def latest_earnings(cik: str) -> dict[str, Any] | None:
    sub = http_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    pause()
    recent = sub.get("filings", {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    items = recent.get("items") or []
    docs = recent.get("primaryDocument") or []
    for i, form in enumerate(forms):
        form = str(form)
        item = str(items[i] if i < len(items) else "")
        is_8k = form == "8-K" and "2.02" in item.split(",")
        is_6k = form == "6-K"
        if not is_8k and not is_6k:
            continue
        accession = str(accs[i])
        filed = str(dates[i])
        url = pick_exhibit(cik, accession)
        if not url and i < len(docs) and docs[i]:
            nodash = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/{docs[i]}"
        line = ""
        text = ""
        if url:
            try:
                text = html_text(http_get(url).decode("utf-8", errors="replace"))
                pause()
                line = lead_sentences(text)
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] exhibit {accession}: {exc}")
        if is_6k:
            blob = f"{url} {text[:4000]}".lower()
            if not any(token in blob for token in ("revenue", "results of operations", "earnings")):
                continue
        return {"form": form, "filed": filed, "accession": accession, "url": url, "lead": line}
    return None


def quarter_facts(cik: str) -> tuple[float | None, float | None, str]:
    try:
        doc = http_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] facts {cik}: {exc}")
        return None, None, ""
    pause()
    gaap = (doc.get("facts") or {}).get("us-gaap") or {}
    revenue, period = None, ""
    for tag in REVENUE_TAGS:
        node = gaap.get(tag) or {}
        units = (node.get("units") or {}).get("USD") or []
        best = None
        for row in units:
            if not isinstance(row, dict):
                continue
            if row.get("fp") not in ("Q1", "Q2", "Q3", "Q4"):
                continue
            if row.get("form") not in ("10-Q", "10-K"):
                continue
            end = str(row.get("end") or "")
            start = str(row.get("start") or "")
            val = row.get("val")
            if not end or not start or not isinstance(val, (int, float)):
                continue
            try:
                span = (
                    datetime.fromisoformat(end) - datetime.fromisoformat(start)
                ).days
            except ValueError:
                continue
            # 丢掉年初至今的累计数，只留单季。
            if not 75 <= span <= 100:
                continue
            if best is None or end > best[0]:
                best = (end, float(val))
        if best:
            revenue, period = best[1], best[0]
            break
    eps = None
    node = gaap.get("EarningsPerShareDiluted") or {}
    units = (node.get("units") or {}).get("USD/shares") or []
    best_eps = None
    for row in units:
        if not isinstance(row, dict):
            continue
        if row.get("fp") not in ("Q1", "Q2", "Q3", "Q4"):
            continue
        end = str(row.get("end") or "")
        start = str(row.get("start") or "")
        val = row.get("val")
        if not end or not start or not isinstance(val, (int, float)):
            continue
        try:
            span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
        except ValueError:
            continue
        if not 75 <= span <= 100:
            continue
        if best_eps is None or end > best_eps[0]:
            best_eps = (end, float(val))
    if best_eps and (not period or best_eps[0] == period):
        eps = best_eps[1]
    return revenue, eps, period


def main() -> int:
    print("[crypto-earnings] ticker map")
    ciks = load_ciks()
    filings: list[dict[str, Any]] = []
    for ticker, name in COMPANIES:
        cik = ciks.get(ticker)
        if not cik:
            print(f"[skip] {ticker} 不在 SEC ticker 表")
            continue
        print(f"[crypto-earnings] {ticker} {cik}")
        try:
            filing = latest_earnings(cik)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] filings {ticker}: {exc}")
            filing = None
        revenue, eps, period = quarter_facts(cik)
        if not filing and revenue is None:
            continue
        row: dict[str, Any] = {
            "ticker": ticker,
            "name": name,
            "cik": cik,
            "revenue_usd": revenue,
            "eps_diluted": eps,
            "revenue_period_end": period,
        }
        if filing:
            row.update(filing)
        filings.append(row)
    filings.sort(key=lambda row: str(row.get("filed") or ""), reverse=True)
    top = [
        {"name": row["name"], "symbol": row["ticker"], "revenue_usd": row["revenue_usd"]}
        for row in filings
        if isinstance(row.get("revenue_usd"), (int, float))
    ]
    top.sort(key=lambda row: float(row["revenue_usd"]), reverse=True)
    doc = {
        "name": "crypto_earnings",
        "source": "SEC EDGAR",
        "source_url": "https://www.sec.gov/edgar",
        "note": "仅美国上市且在 SEC 申报的加密相关公司。不含未上市交易所，不含只在其他市场披露的公司。营收为 XBRL 最近一季，不是新闻稿里的分部收入。",
        "updated_at": now_iso(),
        "company_count": len(filings),
        "filings": filings,
        "top": top[:12],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[crypto-earnings] wrote {OUT_PATH} n={len(filings)}")
    for row in filings:
        print(
            f"  {row.get('ticker')} {row.get('form')} {row.get('filed')} "
            f"rev={row.get('revenue_usd')} eps={row.get('eps_diluted')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
