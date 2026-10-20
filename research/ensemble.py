exec(open(__import__("os").path.join(__import__("os").path.dirname(__file__),"walkforward.py")).read().split("res={}")[0])
import json
for tv in [0.4,0.6]:
    w=sum(weights(P,N,tv,CORE) for N in [50,100,200])/3
    e,t=sim(P,w); s=stats(e.loc["2019":]); s["turn"]=round(t/len(e)*365,1)
    s["yearly"]={y:round(e.loc[f"{y-1}-12-31":f"{y}-12-31"].pipe(lambda x:x.iloc[-1]/x.iloc[0]*100-100),1) for y in range(2019,2027)}
    s["2022_dd"]=round((e.loc["2021-11":"2022-12"]/e.loc["2021-11":"2022-12"].cummax()-1).min()*100,1)
    print(tv,json.dumps(s))
w=sum(weights(P,N,0.4,CORE) for N in [50,100,200])/3
print(w.tail(3).round(3)); print(P[CORE].tail(1)); print((P[CORE].iloc[-1]/P[CORE].rolling(50).mean().iloc[-1]).round(3).to_dict(),(P[CORE].iloc[-1]/P[CORE].rolling(200).mean().iloc[-1]).round(3).to_dict())
