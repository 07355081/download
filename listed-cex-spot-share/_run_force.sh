#!/bin/bash
set -e
python3 - <<'PY'
from pathlib import Path
p = Path("/tmp/_verify_listed.sh")
if p.is_file():
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
PY
bash /tmp/_verify_listed.sh
