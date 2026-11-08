# -*- coding: utf-8 -*-
import pandas as pd
from _paths import MAJOR_VOL_CSV

df = pd.read_csv(MAJOR_VOL_CSV, encoding="utf-8-sig")
col = "coinbase_international_derivatives"
df["_d"] = pd.to_datetime(df.iloc[:, 0])
s = df[["_d", col]].dropna()
print("last non-null", s.tail(5).to_string())
print("count", len(s), "min", s["_d"].min(), "max", s["_d"].max())
