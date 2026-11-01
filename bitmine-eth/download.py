"""Bitmine 每周 ETH 持仓 + 最新一份 8-K 财库包。

周序列仍来自 bmnrscan。质押、现金、ORBS、总资产来自 SEC 最新周报（免费）。
9 月 21 日必须仍是 5983940，对不上就停。
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "bitmine_eth.json"
PAGE = "https://bmnrscan.com/en/eth-holdings"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK0001829311.json"
SEC_ARCH = "https://www.sec.gov/Archives/edgar/data/1829311"
UA = "WuBlock-Dashboard/bitmine-eth (https://data.wublock123.com)"
CHECK_DAY = "2026-09-21"
CHECK_ETH = 5_983_940
PAIR = re.compile(r'date\\?":\\?"(\d{4}-\d{2}-\d{2})\\?",\\?"value\\?":(\d+)')
MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str) -> str:
    last: Exception | None = None
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def parse_holdings(text: str) -> list[dict]:
    by_day: dict[str, int] = {}
    for day, raw in PAIR.findall(text):
        value = int(raw)
        if 1_000_000 <= value <= 20_000_000:
            by_day[day] = value
    if by_day.get(CHECK_DAY) != CHECK_ETH:
        raise RuntimeError(
            f"{CHECK_DAY} 持仓是 {by_day.get(CHECK_DAY)}，不是 {CHECK_ETH}，页面可能变了"
        )
    rows = []
    prev = None
    for day in sorted(by_day):
        eth = by_day[day]
        # value 供通用 lark-feeds 折线识别（页面也认 eth）
        row = {"time": day, "eth": eth, "value": eth}
        if prev is not None:
            row["week_add"] = eth - prev
        prev = eth
        rows.append(row)
    if len(rows) < 8:
        raise RuntimeError(f"持仓点太少: {len(rows)}")
    return rows


def html_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text)


def to_day(blob: str) -> str | None:
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),\s+(\d{4})",
        blob,
    )
    if not match:
        return None
    return f"{int(match.group(3)):04d}-{MONTHS[match.group(1)]:02d}-{int(match.group(2)):02d}"


def money_usd(raw: str, unit: str) -> int:
    value = float(raw)
    if unit.lower().startswith("billion"):
        return int(round(value * 1_000_000_000))
    return int(round(value * 1_000_000))


def parse_pack(text: str) -> dict[str, Any] | None:
    if "ETH Holdings Reach" not in text and "comprised of" not in text:
        return None
    if "Bitmine" not in text and "BMNR" not in text:
        return None

    eth_m = re.search(r"comprised of\s+([\d,]+)\s+ETH\s+at\s+\$", text, re.I)
    if not eth_m:
        return None
    eth = int(eth_m.group(1).replace(",", ""))
    if not (1_000_000 <= eth <= 20_000_000):
        return None

    as_of = None
    idx = text.lower().find("comprised of")
    window = text[max(0, idx - 240) : idx + 40] if idx >= 0 else ""
    asof_m = re.search(
        r"As of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        window,
        re.I,
    )
    if asof_m:
        as_of = to_day(asof_m.group(1))

    add_m = re.search(r"we acquired\s+([\d,]+)\s+ETH", text, re.I)
    staked_m = re.search(r"total staked ETH stands at\s+([\d,]+)", text, re.I)
    if not staked_m:
        staked_m = re.search(r"has\s+([\d,]+)\s+staked ETH", text, re.I)
    cash_m = re.search(
        r"total cash(?:\s*&\s*marketable securities)? of\s+\$([\d.]+)\s+(million|billion)",
        text,
        re.I,
    )
    orbs_m = re.search(
        r"\$([\d.]+)\s+(million|billion)\s+(?:stake in|of)\s+Eightco",
        text,
        re.I,
    )
    total_m = re.search(
        r"holdings totaling\s+\$([\d.]+)\s+(million|billion)",
        text,
        re.I,
    )
    if not total_m:
        total_m = re.search(
            r'Moonshots"?\s+total\s+\$([\d.]+)\s+(million|billion)',
            text,
            re.I,
        )
    pct_m = re.search(r"are\s+([\d.]+)%\s+of the ETH supply", text, re.I)
    if not pct_m:
        pct_m = re.search(r"owns\s+([\d.]+)%\s+of the total ETH", text, re.I)
    rev_m = re.search(
        r"annualized staking revenues(?:\s+are now(?:\s+projected)?)?\s+(?:at\s+)?\$([\d.]+)\s+(million|billion)",
        text,
        re.I,
    )
    btc_m = re.search(r"([\d,]+)\s+Bitcoin\s*\(BTC\)", text, re.I)

    pack: dict[str, Any] = {"eth": eth}
    if as_of:
        pack["as_of"] = as_of
    if add_m:
        pack["week_add"] = int(add_m.group(1).replace(",", ""))
    if staked_m:
        staked = int(staked_m.group(1).replace(",", ""))
        if 100_000 <= staked <= eth:
            pack["staked"] = staked
    if cash_m:
        pack["cash_usd"] = money_usd(cash_m.group(1), cash_m.group(2))
    if orbs_m:
        pack["orbs_usd"] = money_usd(orbs_m.group(1), orbs_m.group(2))
    if total_m:
        pack["total_usd"] = money_usd(total_m.group(1), total_m.group(2))
    if pct_m:
        pack["supply_pct"] = float(pct_m.group(1))
    if rev_m:
        pack["staking_rev_usd"] = money_usd(rev_m.group(1), rev_m.group(2))
    if btc_m:
        pack["btc"] = int(btc_m.group(1).replace(",", ""))
    if not any(k in pack for k in ("staked", "cash_usd", "total_usd")):
        return None
    return pack


def exhibit_names(index_raw: str) -> list[str]:
    names: list[str] = []
    try:
        doc = json.loads(index_raw)
    except json.JSONDecodeError:
        doc = None
    items = []
    if isinstance(doc, dict):
        directory = doc.get("directory")
        if isinstance(directory, dict):
            raw_items = directory.get("item") or []
            items = raw_items if isinstance(raw_items, list) else [raw_items]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if re.search(r"ex[-_]?99|exhibit[-_]?99", name, re.I):
            names.append(name)
    for fallback in ("ex99-1.htm", "ex99.1.htm", "ex99-1.html"):
        if fallback not in names:
            names.append(fallback)
    return names


def recent_8k(doc: dict[str, Any], limit: int = 10) -> list[dict[str, str]]:
    recent = (doc.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    out: list[dict[str, str]] = []
    for form, acc, day in zip(forms, accs, dates):
        if str(form) != "8-K":
            continue
        out.append({"accession": str(acc), "filing_date": str(day)})
        if len(out) >= limit:
            break
    return out


def fetch_latest_pack() -> dict[str, Any] | None:
    print("[bitmine-eth] fetch SEC 8-K pack…")
    submissions = json.loads(fetch(SEC_SUBMISSIONS))
    for row in recent_8k(submissions):
        acc = row["accession"]
        acc_dir = acc.replace("-", "")
        base = f"{SEC_ARCH}/{acc_dir}"
        try:
            names = exhibit_names(fetch(f"{base}/index.json"))
        except RuntimeError:
            names = ["ex99-1.htm", "ex99.1.htm"]
        time.sleep(0.25)
        for name in names:
            try:
                raw = fetch(f"{base}/{name}")
            except RuntimeError:
                continue
            pack = parse_pack(html_text(raw))
            if not pack:
                continue
            pack["filing_date"] = row["filing_date"]
            pack["accession"] = acc
            pack["source"] = f"{base}/{name}"
            print(
                f"[bitmine-eth] pack filing={row['filing_date']} as_of={pack.get('as_of')} "
                f"eth={pack['eth']} staked={pack.get('staked')} cash={pack.get('cash_usd')}"
            )
            return pack
        time.sleep(0.25)
    print("[bitmine-eth] no 8-K pack")
    return None


def main() -> None:
    print("[bitmine-eth] fetch holdings page…")
    rows = parse_holdings(fetch(PAGE))
    last = rows[-1]
    snap: dict[str, Any] = {
        "name": "bitmine_eth",
        "source": PAGE,
        "note_zh": "Bitmine 周持仓来自公开页；质押、现金、ORBS、总资产来自最新一份 SEC 8-K。",
        "updated_at": now_iso(),
        "as_of": last["time"],
        "history": rows,
    }
    try:
        pack = fetch_latest_pack()
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"[bitmine-eth] pack skipped: {exc}")
        pack = None
    if pack:
        snap["latest_pack"] = pack
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[bitmine-eth] wrote {OUT_PATH} as_of={last['time']} "
        f"eth={last['eth']} week_add={last.get('week_add')} n={len(rows)} "
        f"pack={bool(pack)}"
    )


if __name__ == "__main__":
    main()
