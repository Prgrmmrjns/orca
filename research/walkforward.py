import pandas as pd,numpy as np,json,itertools
P=pd.read_csv(__import__("os").path.join(__import__("os").path.dirname(__file__),"closes.csv"),index_col=0,parse_dates=True).loc[:"2026-10-19"]
FEE=0.005  # 0.4% taker + ~0.1% spread per side
CORE=["BTC","ETH","SOL","XRP"]
def stats(eq):
    r=eq.pct_change().dropna(); yrs=len(r)/365
    return dict(cagr=round(((eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1)*100,1),sharpe=round(r.mean()/r.std()*np.sqrt(365),2),
      mdd=round((eq/eq.cummax()-1).min()*100,1),mult=round(eq.iloc[-1]/eq.iloc[0],2))
def weights(P,N,tv,uni,gate=True,cap=0.5,mode="trend"):
    sma=P.rolling(N).mean(); vol=P.pct_change().rolling(30).std()*np.sqrt(365)
    btc_on=P.BTC>sma.BTC
    on=(P>sma)&P[uni].notna().reindex(columns=P.columns,fill_value=False)
    on=on[uni]
    if gate: on=on.mul(btc_on,axis=0)
    if mode=="xsmom":
        mom=P[uni].pct_change(14); rk=mom.where(P[uni].notna()).rank(axis=1,ascending=False)
        w=((rk==1)&(mom>0)).astype(float)*0.95; return w.fillna(0)
    iv=(1/vol[uni]).where(on.astype(bool)); w=iv.div(iv.sum(axis=1),axis=0).fillna(0)
    # portfolio vol scaling (approx, ignore corr -> use avg vol of held * 0.85)
    pv=(w*vol[uni]).sum(axis=1)*0.85
    scale=(tv/pv).clip(upper=1).fillna(0)
    w=w.mul(scale,axis=0).clip(upper=cap)
    return w.fillna(0)
def run(P,w,every=7,band=0.05):
    R=P[w.columns].pct_change().fillna(0)
    eq=1.0;cur=pd.Series(0.0,index=w.columns);out=[];fees=0;turn=0
    for i,d in enumerate(P.index):
        cur=cur*(1+R.loc[d]); eq_g=eq*(1+(cur.sum()-cur.sum()/(1+0) if False else 0))
        # value tracking
        val=eq*(1-cur.sum()/(1) ) if False else None
        out.append(None)
    return None
def sim(P,w,every=7,band=0.05):
    R=P[w.columns].pct_change().fillna(0).values; W=w.shift(1).fillna(0).values  # signal at close d, trade next close
    cash=1.0;pos=np.zeros(W.shape[1]);eqs=[];tv=0
    for i in range(len(R)):
        pos=pos*(1+R[i]); eq=cash+pos.sum()
        if i%every==0:
            tgt=W[i]*eq; diff=tgt-pos
            diff[np.abs(diff)<band*eq]=0
            if (W[i]==0).all() or True:
                diff[(W[i]==0)&(pos>0)]=-pos[(W[i]==0)&(pos>0)]  # always fully exit off names
            cost=np.abs(diff).sum()*FEE; tv+=np.abs(diff).sum()/eq
            pos=pos+diff; cash=cash-diff.sum()-cost; eq=cash+pos.sum()
        eqs.append(eq)
    return pd.Series(eqs,index=P.index),tv
res={}
start="2019-01-01"
Pw=P.loc["2017-08-17":]
def bench():
    b=P.BTC.loc[start:]; res["BTC_BH"]=stats(b)
    for nm,u in [("EW_core_BH",CORE),("EW_all_monthly",list(P.columns))]:
        w=P[u].notna().astype(float); w=w.div(w.sum(axis=1),axis=0)
        e,_=sim(P.loc[start:],w.loc[start:],every=30,band=0.0); res[nm]=stats(e)
bench()
grid=list(itertools.product([50,100,150,200],[0.4,0.6]))
for uname,uni in [("core",CORE),("all",list(P.columns))]:
    # in-sample-free walk-forward: each year pick best (N,tv) by Sharpe on all prior data (from 2018)
    W={k:weights(P,k[0],k[1],uni) for k in grid}
    full={k:sim(P,W[k]) for k in grid}
    pieces=[];picks={}
    for y in range(2019,2027):
        best=max(grid,key=lambda k: stats(full[k][0].loc["2018-03-01":f"{y-1}-12-31"])["sharpe"])
        picks[y]=best
        r=full[best][0].loc[f"{y-1}-12-31":f"{y}-12-31"].pct_change().dropna(); pieces.append(r)
    eq=(1+pd.concat(pieces)).cumprod()
    res[f"WF_trend_{uname}"]=dict(**stats(eq),picks={y:list(v) for y,v in picks.items()})
    for k in [(100,0.6),(150,0.6),(200,0.6),(50,0.6)]:
        e,tv=full[k]; res[f"fixed_{uname}_N{k[0]}_tv{k[1]}"]=dict(**stats(e.loc[start:]),turnover_yr=round(tv/len(e)*365,1))
    res[f"WF_yearly_{uname}"]={y:round(pieces[i].add(1).prod()*100-100,1) for i,y in enumerate(range(2019,2027))}
# BTC-only trend
for N in [100,150,200]:
    e,tv=sim(P,weights(P,N,9,["BTC"],cap=1.0)); res[f"BTC_trend_N{N}"]=stats(e.loc[start:])
# old-style xs momentum single coin
e,tv=sim(P,weights(P,14,0,list(P.columns),gate=False,mode="xsmom"),band=0.0); res["xsmom_top1_14d_all"]=dict(**stats(e.loc[start:]),turnover_yr=round(tv/len(e)*365,1))
for nm in ["BTC"]:
    res["BTC_BH_yearly"]={y:round(P.BTC.loc[f"{y-1}-12-31":f"{y}-12-31"].pipe(lambda s:s.iloc[-1]/s.iloc[0]*100-100),1) for y in range(2019,2027)}
json.dump(res,open("bt.json","w"),indent=1,default=str); print(json.dumps(res,indent=1,default=str))
