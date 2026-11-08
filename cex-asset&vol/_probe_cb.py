# -*- coding: utf-8 -*-
import pandas as pd
from _paths import MAJOR_VOL_CSV

df = pd.read_csv(MAJOR_VOL_CSV, encoding="utf-8-sig")
print([c for c in df.columns if "coin" in c.lower() or c in ("gdax",)])
df["_d"] = pd.to_datetime(df.iloc[:, 0])
m = (df["_d"] >= "2026-09-01") & (df["_d"] <= "2026-10-31")
for c in df.columns:
    if "coin" in c.lower() or c == "gdax":
        print(c, "nn", df.loc[m, c].notna().sum(), "sep-oct sample")
        print(df.loc[m, ["_d", c]].tail(10).to_string())
