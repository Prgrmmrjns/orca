import json,urllib.request,time,pandas as pd
syms="BTC ETH SOL XRP ADA DOGE LTC LINK AVAX DOT BCH TRX XLM ATOM UNI".split()
def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30).read())
out={}
for s in syms:
    rows=[];start=1500000000000
    while True:
        r=get(f"https://data-api.binance.vision/api/v3/klines?symbol={s}USDT&interval=1d&startTime={start}&limit=1000")
        if not r: break
        rows+=r; start=r[-1][0]+86400000
        if len(r)<1000: break
    out[s]=pd.Series({pd.Timestamp(x[0],unit='ms'):float(x[4]) for x in rows})
    print(s,len(rows),out[s].index[0].date())
pd.DataFrame(out).to_csv(__import__("os").path.join(__import__("os").path.dirname(__file__),"closes.csv"))
