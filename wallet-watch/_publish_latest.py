# -*- coding: utf-8 -*-
"""把已有 events.json 写成飞书页 latest.json，不拉链、不推送。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from monitor import load_json, publish_feed  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "output" / "events.json"
    doc = load_json(path, {})
    if not isinstance(doc, dict):
        raise SystemExit(f"bad events {path}")
    events = doc.get("events") if isinstance(doc.get("events"), list) else []
    publish_feed(str(doc.get("case") or ""), events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
