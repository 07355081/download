#!/bin/bash
set -euo pipefail
cd /root/data-download
python3 - <<'PY'
import json
from pathlib import Path
for name in (
    "/tmp/_do_force_push.sh",
    "/root/data-download/listed-cex-spot-share/flash.py",
):
    p = Path(name)
    if p.is_file():
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
d = json.loads(Path("listed-cex-spot-share/output/json/listed_cex_spot_share.json").read_text())
print("as_of", d.get("as_of"), "shares", d["latest"]["shares"], "gap", d["latest"]["gap_pp"])
PY
/root/data-download/.venv/bin/python listed-cex-spot-share/flash.py --push --force
