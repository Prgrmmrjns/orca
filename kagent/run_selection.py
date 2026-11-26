"""Round 2 (2026-11-26): walk-forward selection over the regime-trend family. python -m kagent.run_selection"""
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np, itertools
from kagent.data import load
from kagent import strategies as S, backtest as B, agent
C=load('close').ffill(limit=3).loc["2018-01-01":]
fam={}
for buf,sl,uni,tv in itertools.product([0,0.02,0.05],[0,10,20],["BTC","core4","top8"],[0.2,0.3]):
    u=agent.UNIVERSE if uni=="top8" else agent.UNIVERSE[:4]
    fam[(buf,sl,uni,tv)]=S.weekly(S.cap(S.regime_trend2(C,u,tv=tv,buffer=buf,slope=sl,btc_only=uni=="BTC"),1.0))
for sb in (0.5,1.0):
    fam[("short",sb,"top8",0.3)]=S.weekly(S.cap(S.regime_trend(C,agent.UNIVERSE,agent.WINDOWS,0.3,True,sb),1.0))
rets={k:B.simulate(C,w,band=.05).equity.pct_change().fillna(0) for k,w in fam.items()}
R=pd.DataFrame(rets); R.columns=[str(c) for c in R.columns]
R.to_csv('research/kagent_report/family_returns.csv.gz')
def sh(x): return x.mean()/x.std()*np.sqrt(365) if x.std()>0 else -9
def calmar(x):
    e=(1+x).cumprod(); dd=-(e/e.cummax()-1).min(); return (e.iloc[-1]**(365/len(x))-1)/dd if dd>0 else 0
out={}
for crit in ("sharpe","calmar"):
  for lb in (730,1095):
    pieces=[];picks=[]
    for d in pd.date_range("2021-01-01","2026-11-01",freq="6MS"):
        hist=R.loc[d-pd.Timedelta(days=lb):d-pd.Timedelta(days=1)]
        sc=hist.apply(sh if crit=="sharpe" else calmar); best=sc.idxmax(); picks.append((d.date(),best))
        pieces.append(R.loc[d:d+pd.DateOffset(months=6)-pd.Timedelta(days=1),best])
    r=pd.concat(pieces); eq=(1+r).cumprod()
    out[(crit,lb)]=eq
    for lab,(s,e) in {"2021-24":("2021-01-01","2024-11-30"),"holdout":("2024-12-01",None)}.items():
        print(crit,lb,lab,{k:v for k,v in B.stats(eq.loc[s:e]).items() if k in("cagr","sharpe","mdd")})
    print("  picks",picks)
# distribution of holdout over the family (reporting only)
H=R.loc["2024-12-01":]; hs=H.apply(sh); hc=((1+H).prod()**(365/len(H))-1)*100
print("family holdout: share positive CAGR",(hc>0).mean().round(2),"median CAGR",hc.median().round(1),"median sharpe",hs.median().round(2),"best",hc.max().round(1),"worst",hc.min().round(1))
D=R.loc["2020-01-01":"2024-11-30"]; print("dev->holdout rank corr", pd.Series(D.apply(sh)).corr(hs,method="spearman").round(2))
