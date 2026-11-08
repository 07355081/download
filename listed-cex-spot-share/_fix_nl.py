#!/usr/bin/env python3
from pathlib import Path

for name in ("download.py", "flash.py", "_patch_feed.js"):
    path = Path(__file__).resolve().parent / name
    if not path.is_file():
        continue
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    path.write_bytes(data)
    print(name, "ok", [line for line in data.decode().splitlines() if "urllib" in line])
