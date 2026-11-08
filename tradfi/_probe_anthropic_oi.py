# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lake import connect  # noqa: E402

ROOT = "/mnt/e/data-lake" if Path("/mnt/e/data-lake").is_dir() else "E:/data-lake"
con = connect(ROOT)
rows = con.execute(
    """
    SELECT CAST(to_timestamp(time/1000.0) AS DATE) AS d,
           exchange,
           arg_max(oi_usd, time) AS oi
    FROM tradfi_oi
    WHERE interval='1d'
      AND (upper(symbol) LIKE '%ANTHROPIC%' OR upper(coalesce(base_asset,''))='ANTHROPIC')
    GROUP BY 1,2
    ORDER BY 1 DESC, 3 DESC
    LIMIT 50
    """
).fetchall()
for r in rows:
    print(r)

for start, end in [("2026-07-15", "2026-09-12"), ("2026-08-15", "2026-10-13")]:
    q = f"""
    WITH daily AS (
      SELECT CAST(to_timestamp(time/1000.0) AS DATE) AS d,
             exchange, arg_max(oi_usd, time) AS oi
      FROM tradfi_oi
      WHERE interval='1d'
        AND (upper(symbol) LIKE '%ANTHROPIC%' OR upper(coalesce(base_asset,''))='ANTHROPIC')
      GROUP BY 1,2
    )
    SELECT CAST(d AS VARCHAR), sum(oi) AS total,
           sum(CASE WHEN exchange='Binance' THEN oi ELSE 0 END) AS bn
    FROM daily WHERE d IN (DATE '{start}', DATE '{end}')
    GROUP BY 1 ORDER BY 1
    """
    print(start, "->", end, con.execute(q).fetchall())
