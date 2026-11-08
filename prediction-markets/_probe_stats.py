import json
from pathlib import Path

p = Path(__file__).resolve().parent / "output" / "json" / "prediction_markets_quarterly.json"
doc = json.loads(p.read_text(encoding="utf-8"))
qs = doc["quarters"]
for q in qs[-4:]:
    top = ", ".join(f"{x['name']} {x['volume_usd']/1e9:.2f}B" for x in q["protocols"][:3])
    print(q["quarter"], f"{q['volume_usd']/1e9:.2f}B", top)
a, b = qs[-2], qs[-1]
print("QoQ", f"{(b['volume_usd']/a['volume_usd']-1)*100:.1f}%")
lookback = qs[:-1][-4:]
print("high?", b["volume_usd"] >= max(x["volume_usd"] for x in lookback))
