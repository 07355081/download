"""Drop reconcile rows for billing periods Dune no longer returns.

The live usage API on 2026-10-16 only reports the new period (0 used).
The local 14-day window still counted the previous period's reconcile
and blocked every execute until 2026-10-19.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ledger = Path("/root/data-download/.dune-credit-ledger.json")
backup = ledger.with_name(
    f".dune-credit-ledger.bak-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
)
shutil.copy2(ledger, backup)
doc = json.loads(ledger.read_text(encoding="utf-8"))
kept = []
dropped = []
today = datetime.now(timezone.utc).date().isoformat()
for entry in doc.get("entries") or []:
    if entry.get("kind") == "reconcile" and str(entry.get("period_end") or "") <= today:
        dropped.append(entry)
        continue
    kept.append(entry)
doc["entries"] = kept
ledger.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print("backup", backup)
print("dropped", len(dropped))
print("kept", len(kept))
