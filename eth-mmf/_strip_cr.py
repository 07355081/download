# -*- coding: utf-8 -*-
from pathlib import Path
import sys

for raw in sys.argv[1:]:
    path = Path(raw)
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    path.write_bytes(data)
    print("lf", path)
