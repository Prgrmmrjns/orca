"""Event-reaction backtest on 1-minute candles (Fed decisions, CPI releases).

Question: after a scheduled macro release, can a bot that only sees the move after 1-15 minutes still profit by
following (momentum) or fading the first move over the next 15 min - 24 h, after Kraken perp fees + slippage?
Release times: FOMC statements 14:00 New York time (scheduled meetings only), CPI 08:30 New York time.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FOMC = ['20180131', '20180321', '20180502', '20180613', '20180801', '20180926', '20181108', '20181219',
        '20190130', '20190320', '20190501', '20190619', '20190731', '20190918', '20191030', '20191211',
        '20200129', '20200429', '20200610', '20200729', '20200916', '20201105', '20201216',
        '20210127', '20210317', '20210428', '20210616', '20210728', '20210922', '20211103', '20211215',
        '20220126', '20220316', '20220504', '20220615', '20220727', '20220921', '20221102', '20221214',
        '20230201', '20230322', '20230503', '20230614', '20230726', '20230920', '20231101', '20231213',
        '20240131', '20240320', '20240501', '20240612', '20240731', '20240918', '20241107', '20241218',
        '20250129', '20250319', '20250507', '20250618', '20250730', '20250917', '20251029', '20251210',
        '20260128', '20260318', '20260429', '20260617', '20260729', '20260916', '20261028']
COST_RT = 2 * (0.0005 + 0.0010)  # perp taker 0.05% + 0.10% event slippage, each side


def event_times(cpi_dates: list[str]) -> pd.DataFrame:
    rows = [(pd.Timestamp(d + " 14:00", tz="America/New_York"), "FOMC") for d in FOMC]
    rows += [(pd.Timestamp(d + " 08:30", tz="America/New_York"), "CPI") for d in cpi_dates]
    ev = pd.DataFrame(rows, columns=["t", "kind"])
    ev["t"] = ev.t.dt.tz_convert("UTC").dt.tz_localize(None)
    return ev.sort_values("t").reset_index(drop=True)


def reactions(px: pd.Series, ev: pd.DataFrame, entries=(1, 5, 15), holds=(15, 60, 240, 1440)) -> pd.DataFrame:
    """px: 1-minute close series indexed by bar OPEN time (UTC). Price 'at' T = close of the bar opening at T-1min."""
    def at(T):
        i = px.index.searchsorted(T - pd.Timedelta(minutes=1), side="right") - 1
        return float(px.iloc[i]) if i >= 0 and (T - px.index[i]) <= pd.Timedelta(minutes=5) else np.nan
    out = []
    for _, e in ev.iterrows():
        t0 = e.t
        p0 = at(t0)
        row = {"t": t0, "kind": e.kind, "pre60": at(t0) / at(t0 - pd.Timedelta(minutes=60)) - 1}
        for en in entries:
            pe = at(t0 + pd.Timedelta(minutes=en))
            row[f"first{en}"] = pe / p0 - 1
            for h in holds:
                row[f"fwd{en}_{h}"] = at(t0 + pd.Timedelta(minutes=en + h)) / pe - 1
        out.append(row)
    return pd.DataFrame(out)


def evaluate(R: pd.DataFrame, entries=(1, 5, 15), holds=(15, 60, 240, 1440), min_move=0.0) -> pd.DataFrame:
    rows = []
    for en in entries:
        for h in holds:
            d = R[[f"first{en}", f"fwd{en}_{h}", "kind", "t"]].dropna()
            d = d[d[f"first{en}"].abs() >= min_move]
            sig = np.sign(d[f"first{en}"])
            for mode, s in (("follow", sig), ("fade", -sig)):
                g = s * d[f"fwd{en}_{h}"]
                net = g - COST_RT
                rows.append(dict(entry_min=en, hold_min=h, mode=mode, n=len(g), gross_bps=round(g.mean() * 1e4, 1),
                                 net_bps=round(net.mean() * 1e4, 1), hit=round((net > 0).mean(), 2),
                                 t=round(net.mean() / net.std() * np.sqrt(len(net)), 2) if len(net) > 2 else np.nan))
    return pd.DataFrame(rows)
