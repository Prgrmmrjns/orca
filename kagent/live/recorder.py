"""Record 1-minute Kraken Futures bars for coins + stock/commodity perps from now on, so minute-level news backtests
for stocks get a dataset (Kraken stock perps have no long history). Runs next to kagent.newslat (news timestamps).
python -m kagent.live.recorder --hours 24   -> research/kagent_live/m1/<YYYY-MM-DD>/<symbol>.csv"""
from __future__ import annotations

import argparse, json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

from kagent.data import CRYPTO
from kagent.minute_data import KRAKEN_PERPS

SYMS = list(CRYPTO.values()) + KRAKEN_PERPS + ["PF_TSLAXUSD", "PF_MUUSD", "PF_INTCUSD", "PF_SOXLUSD", "PF_XAGUSD", "PF_NATGASUSD"]
OUT = "research/kagent_live/m1"


def pull(sym, last):
    now = int(time.time())
    d = json.loads(urllib.request.urlopen(
        f"https://futures.kraken.com/api/charts/v1/trade/{sym}/1m?from={now - 600}&to={now}", timeout=20).read())
    rows = [c for c in d.get("candles", []) if c["time"] // 1000 > last.get(sym, 0) and c["time"] // 1000 < now // 60 * 60]
    for c in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(c["time"] // 1000))
        os.makedirs(f"{OUT}/{day}", exist_ok=True)
        with open(f"{OUT}/{day}/{sym}.csv", "a") as f:
            f.write(f'{c["time"]},{c["open"]},{c["high"]},{c["low"]},{c["close"]},{c["volume"]}\n')
        last[sym] = c["time"] // 1000


def main(hours):
    last, end = {}, time.time() + hours * 3600
    with ThreadPoolExecutor(6) as ex:
        while time.time() < end:
            list(ex.map(lambda s: _safe(pull, s, last), SYMS))
            time.sleep(60 - time.time() % 60 + 3)


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--hours", type=float, default=24)
    main(ap.parse_args().hours)
