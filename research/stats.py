import pandas as pd,numpy as np,json
P=pd.read_csv(__import__("os").path.join(__import__("os").path.dirname(__file__),"closes.csv"),index_col=0,parse_dates=True).loc[:"2026-10-19"]
R=P.pct_change()
out={}
def stats(eq):
    r=eq.pct_change().dropna(); yrs=len(r)/365
    return dict(cagr=round((eq.iloc[-1]/eq.iloc[0])**(1/yrs)*100-100,1),vol=round(r.std()*np.sqrt(365)*100,1),
      sharpe=round(r.mean()/r.std()*np.sqrt(365),2) if r.std()>0 else 0,mdd=round((eq/eq.cummax()-1).min()*100,1),mult=round(eq.iloc[-1]/eq.iloc[0],2))
# descriptive
core=["BTC","ETH","SOL","XRP"]
desc={}
for s in P:
    r=R[s].dropna(); p=P[s].dropna()
    desc[s]=dict(start=str(p.index[0].date()),annvol=round(r.std()*np.sqrt(365)*100),mdd=round((p/p.cummax()-1).min()*100),
      ac1d=round(r.autocorr(1),3),ac7=round(p.pct_change(7)[::7].autocorr(1),3))
out["desc"]=desc
out["corr_2023+"]=R.loc["2023":,core].corr().round(2).to_dict()
# TS momentum predictive: sign of past L-day return vs next 7d return, pooled
tsm={}
for L in [7,14,30,60,90,120,200]:
    rows=[]
    for s in P:
        p=P[s].dropna(); past=p.pct_change(L); fut=p.shift(-7)/p-1
        d=pd.DataFrame({"past":past,"fut":fut}).dropna()[::7]; rows.append(d)
    d=pd.concat(rows)
    tsm[L]=dict(up_fut=round(d[d.past>0].fut.mean()*100,2),dn_fut=round(d[d.past<=0].fut.mean()*100,2),n=len(d))
out["tsmom_next7d_pct"]=tsm
# BTC above/below 200d SMA forward 30d
b=P.BTC; sma=b.rolling(200).mean(); f30=b.shift(-30)/b-1
out["btc_fwd30_above200"]=round(f30[b>sma].mean()*100,2); out["btc_fwd30_below200"]=round(f30[b<=sma].mean()*100,2)
# alts vs BTC regime: alt fwd 30d when BTC above vs below
alts=[c for c in P if c!="BTC"]
af=(P[alts].shift(-30)/P[alts]-1).mean(axis=1)
out["alt_fwd30_btc_above"]=round(af[b>sma].mean()*100,2); out["alt_fwd30_btc_below"]=round(af[b<=sma].mean()*100,2)
# ETH/BTC ratio trend
json.dump(out,open("findings.json","w"),indent=1,default=str); print(json.dumps(out,indent=1,default=str))
