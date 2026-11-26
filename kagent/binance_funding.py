"""Binance USD-M funding history (monthly zips from data.binance.vision), daily sum per coin.
Used as the funding signal and as the cost proxy where Kraken's funding history (about 1 year) is missing."""
import io, urllib.request, zipfile
import pandas as pd
from kagent.data import CRYPTO, DATA


def fetch(coin, months):
    out = []
    for m in months:
        u = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{coin}USDT/{coin}USDT-fundingRate-{m}.zip"
        try:
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=30).read()))
            df = pd.read_csv(z.open(z.namelist()[0]))
            out.append(df)
        except Exception:
            pass
    if not out:
        return pd.Series(dtype=float)
    df = pd.concat(out)
    t = pd.to_datetime(df.iloc[:, 0], unit="ms")
    return pd.Series(df["last_funding_rate"].values, index=t).sort_index()


def build():
    months = [p.strftime("%Y-%m") for p in pd.period_range("2019-09", pd.Timestamp.today() - pd.offsets.MonthBegin(1), freq="M")]
    d = {}
    for c in CRYPTO:
        s = fetch(c, months)
        d[c] = s.resample("1D").sum(min_count=1)
        print(c, len(s))
    pd.DataFrame(d).to_csv(DATA / "binance_funding_daily.csv.gz")


if __name__ == "__main__":
    build()
