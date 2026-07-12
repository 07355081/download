"""Step 2/3 — 在 output/holdings.csv 中将指定 entity_id 标为 skip_json=1（标红，不转 JSON）。

在 1.download.py 之后、3.csv_to_json.py 之前运行：
    python 2.mark_holdings.py
"""
from __future__ import annotations

import argparse

from holdings_csv import HOLDINGS_CSV, read_rows, write_rows

# 标红（skip_json=1）：可按需增删 entity_id
RED_ENTITY_IDS = frozenset(
    {
        "china",
        "cango",
        "cimg",
        "gd-culture-group",
        "linekong-interactive-group-co-ltd",
        "nano-labs",
        "next-technology-holding",
        "pop-culture-group-co-ltd",
        "sos-limited",
        "the9-limited",
    }
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    rows = read_rows()
    if not rows:
        raise SystemExit(f"未找到 {HOLDINGS_CSV}，请先运行 1.download.py")

    red_count = 0
    for row in rows:
        eid = str(row.get("entity_id") or "").strip()
        if eid and eid in RED_ENTITY_IDS:
            row["skip_json"] = "1"
            red_count += 1
        else:
            row["skip_json"] = "0"

    write_rows(rows)
    print(f"updated {HOLDINGS_CSV}: {len(rows)} rows, {red_count} marked skip_json=1")


if __name__ == "__main__":
    main()
