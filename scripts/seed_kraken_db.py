"""One-off: seed the orca-agent database with the research history (prices 2018+, funding, alt data).
Run from repo root with SUPABASE_URL, SUPABASE_ANON_KEY, ORCA_DB_KEY set:  python scripts/seed_kraken_db.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))
from kraken import db  # noqa
from kraken.config import STOCK_MAP, SYMS  # noqa

D = Path(__file__).resolve().parent.parent / "research" / "kagent_data"


def rows_long(df, a, b, c):
    s = df.stack().dropna()
    return [{a: k[1], b: k[0].date().isoformat(), c: float(v)} for k, v in s.items() if np.isfinite(v)]


C = pd.read_csv(D / "close.csv.gz", index_col=0, parse_dates=True)[SYMS].loc["2017-01-01":]
db.insert("prices_daily", rows_long(C, "sym", "d", "close"), upsert=True)
print("prices", C.shape)

S = pd.read_csv(D / "stocks_close.csv.gz", index_col=0, parse_dates=True)[list(STOCK_MAP)].loc["2017-01-01":]
S = S.rename(columns=STOCK_MAP)
db.insert("prices_daily", rows_long(S, "sym", "d", "close"), upsert=True)
print("stocks", S.shape)

bf = pd.read_csv(D / "binance_funding_daily.csv.gz", index_col=0, parse_dates=True)
bf.index = bf.index.tz_localize(None) if bf.index.tz is not None else bf.index
kf = pd.read_csv(D / "kraken_funding_hourly.csv.gz", index_col=0, parse_dates=True)
kf.index = kf.index.tz_localize(None) if kf.index.tz is not None else kf.index
kf = kf.resample("1D").sum()
kf = kf.loc[kf.fillna(0).ne(0).any(axis=1).idxmax():]
F = kf.combine_first(bf).reindex(columns=SYMS)
db.insert("funding_daily", rows_long(F, "sym", "d", "rate"), upsert=True)
print("funding", F.shape)

A = pd.read_csv(D / "altdata.csv.gz", index_col=0, parse_dates=True).loc["2017-01-01":]
A.index = A.index.normalize()
A = A.groupby(level=0).last()
rows = [{"d": k[0].date().isoformat(), "k": k[1], "v": float(v)} for k, v in A.stack().dropna().items() if np.isfinite(v)]
db.insert("altdata", rows, upsert=True)
print("alt", len(rows))
