"""Rolling 24h LSK liquidations and minute prices around 2026-09-13. Do not keep."""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

key = None
for line in Path("/root/data-download/coinglass-history/_common.py").read_text(encoding="utf-8").splitlines():
    if line.startswith("COINGLASS_API_KEY"):
        key = line.split("=", 1)[1].strip().strip('"').strip("'")
        break

def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "wu-probe/1"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

start = int(datetime(2026, 9, 11, tzinfo=timezone.utc).timestamp() * 1000)
end = int(datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
exchanges = "Binance,OKX,Bybit,Bitget,Gate,HTX,KuCoin,MEXC"
qs = urllib.parse.urlencode({
    "symbol": "LSK",
    "interval": "1h",
    "limit": "1000",
    "start_time": str(start),
    "end_time": str(end),
    "exchange_list": exchanges,
})
url = "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-history?" + qs
payload = get(url, {"CG-API-KEY": key or "", "accept": "application/json"})
print("cg", payload.get("code"), payload.get("msg"), "n", len(payload.get("data") or []))
rows = []
for row in payload.get("data") or []:
    ts = int(row["time"])
    total = float(row.get("aggregated_long_liquidation_usd") or 0) + float(row.get("aggregated_short_liquidation_usd") or 0)
    rows.append((ts, total))
rows.sort()
target = 38_365_500
best = None
for i, (ts, _) in enumerate(rows):
    window_start = ts - 23 * 3600 * 1000
    s = sum(v for t, v in rows if window_start <= t <= ts)
    diff = abs(s - target)
    if best is None or diff < best[0]:
        best = (diff, s, ts)
print("best_roll", round(best[1], 2), "diff", round(best[0], 2),
      datetime.fromtimestamp(best[2] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M"))
# also print the window ending 2026-09-13 04:00 and 05:00 UTC
for hour in (4, 5, 6):
    end_ts = int(datetime(2026, 9, 13, hour, tzinfo=timezone.utc).timestamp() * 1000)
    window_start = end_ts - 23 * 3600 * 1000
    s = sum(v for t, v in rows if window_start <= t <= end_ts)
    print(f"end_{hour:02d}utc", round(s, 2))

# Binance 1m around the spike
b_start = int(datetime(2026, 9, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
b_end = int(datetime(2026, 9, 13, 8, tzinfo=timezone.utc).timestamp() * 1000)
burl = f"https://api.binance.com/api/v3/klines?symbol=LSKUSDT&interval=1m&startTime={b_start}&endTime={b_end}&limit=500"
try:
    bars = get(burl)
    print("binance_1m", len(bars) if isinstance(bars, list) else bars)
    if isinstance(bars, list) and bars:
        hi = max(bars, key=lambda x: float(x[2]))
        print("binance_max", float(hi[2]), datetime.fromtimestamp(int(hi[0]) / 1000, timezone.utc).strftime("%H:%M"))
        # first bar at or after 04:33 with close near 0.98, and max high before that
        cut = int(datetime(2026, 9, 13, 4, 33, tzinfo=timezone.utc).timestamp() * 1000)
        before = [x for x in bars if int(x[0]) <= cut]
        if before:
            print("binance_high_by_0433", max(float(x[2]) for x in before), "close_0433ish", float(before[-1][4]))
except Exception as exc:
    print("binance_err", getattr(exc, "code", type(exc).__name__))

# OKX 1m
ok_start = datetime(2026, 9, 13, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ok_end = datetime(2026, 9, 13, 8, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ourl = f"https://www.okx.com/api/v5/market/history-candles?instId=LSK-USDT&bar=1m&after={int(datetime(2026,9,13,8,tzinfo=timezone.utc).timestamp()*1000)}&limit=300"
try:
    ok = get(ourl)
    data = ok.get("data") or []
    print("okx", ok.get("code"), "n", len(data))
    if data:
        hi = max(data, key=lambda x: float(x[2]))
        print("okx_max", float(hi[2]), datetime.fromtimestamp(int(hi[0]) / 1000, timezone.utc).strftime("%m-%d %H:%M"))
except Exception as exc:
    print("okx_err", getattr(exc, "code", type(exc).__name__))
