"""One-shot: probe Arc rank then print latest snapshot chains."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import download as d  # noqa: E402

print("CHAINS", d.CHAINS)
toks = d.fetch_chain_rank("arc", limit=5, interval="24h")
vol = sum(t["volume"] for t in toks)
print("arc_probe", len(toks), vol, toks[0]["symbol"] if toks else None)

latest = HERE / "output" / "json" / "meme_markets_24h.json"
if latest.is_file():
    doc = json.loads(latest.read_text(encoding="utf-8"))
    print("latest_total", doc.get("total_volume"))
    for c in doc.get("chains") or []:
        print(f"  {c.get('label')}: {c.get('volume'):.0f} ({c.get('share'):.1f}%)")
    print("top", [t.get("symbol") for t in (doc.get("top_tokens") or [])[:3]])
