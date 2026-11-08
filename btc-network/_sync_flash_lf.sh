#!/bin/bash
python3 - << 'PY'
from pathlib import Path
p = Path("/root/data-download/btc-network/flash.py")
p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
text = p.read_text(encoding="utf-8")
print("lf", p.stat().st_size)
print("news", "吴说获悉" in text)
print("old_alert", "出现明显变化" in text)
PY
