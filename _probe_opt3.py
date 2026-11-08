"""Check options delivery cron and summarize front-expiry history. Do not keep."""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

cache = Path("/root/data-download/coinglass-history/option-max-pain-history/cache")
state = Path("/root/dashboard/data/options-delivery-lark.json")
print("state", state.is_file())
if state.is_file():
    doc = json.loads(state.read_text(encoding="utf-8"))
    ids = doc.get("sentIds") or []
    print("sent", len(ids), "last", ids[-5:] if ids else None, "updated", doc.get("updatedAt"))


def day_of(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")


def load(name):
    doc = json.loads((cache / name).read_text(encoding="utf-8"))
    by_day = defaultdict(list)
    for snap in doc.get("snapshots") or []:
        ts = snap.get("fetch_time_ms")
        if not isinstance(ts, (int, float)):
            continue
        by_day[day_of(ts)].append(snap)
    return by_day


btc = load("BTC_Deribit.json")
eth = load("ETH_Deribit.json")
days = sorted(set(btc) & set(eth))
print("overlap days", len(days), "first", days[0] if days else None, "last", days[-1] if days else None)


def front(snaps, day):
    # last fetch that day; nearest expiry date code >= day YYMMDD
    last_ts = max(s.get("fetch_time_ms") or 0 for s in snaps)
    batch = [s for s in snaps if s.get("fetch_time_ms") == last_ts]
    code = day[2:4] + day[5:7] + day[8:10]
    future = [s for s in batch if str(s.get("date") or "") >= code]
    if not future:
        return None
    row = min(future, key=lambda s: str(s.get("date")))
    call = float(row.get("call_open_interest") or 0)
    put = float(row.get("put_open_interest") or 0)
    notion = float(row.get("call_open_interest_notional") or 0) + float(row.get("put_open_interest_notional") or 0)
    total = 0.0
    for s in batch:
        total += float(s.get("call_open_interest") or 0) + float(s.get("put_open_interest") or 0)
    return {
        "expiry": row.get("date"),
        "contracts": call + put,
        "pcr": (put / call) if call else None,
        "pain": row.get("max_pain_price"),
        "notional": notion,
        "share": (call + put) / total if total else None,
        "total": total,
    }


series = []
for day in days:
    b = front(btc[day], day)
    e = front(eth[day], day)
    if not b or not e:
        continue
    series.append((day, b, e, b["notional"] + e["notional"]))

print("series", len(series))
for row in series[-8:]:
    day, b, e, tot = row
    print(
        day,
        "BTC", b["expiry"], round(b["contracts"]), "pcr", None if b["pcr"] is None else round(b["pcr"], 2),
        "pain", b["pain"], "n", round(b["notional"] / 1e6), "share", round((b["share"] or 0) * 100, 1),
        "ETH", e["expiry"], round(e["contracts"]), "pcr", None if e["pcr"] is None else round(e["pcr"], 2),
        "pain", e["pain"], "n", round(e["notional"] / 1e6), "share", round((e["share"] or 0) * 100, 1),
        "sum_m", round(tot / 1e6),
    )

notions = [r[3] for r in series]
print("notional max", round(max(notions) / 1e6), "at", series[notions.index(max(notions))][0])
print("notional last", round(notions[-1] / 1e6), "p95", round(sorted(notions)[int(len(notions) * 0.95)] / 1e6))
shares = [r[1]["share"] for r in series if r[1]["share"] is not None]
print("btc share max", round(max(shares) * 100, 1), "last", round(shares[-1] * 100, 1))
