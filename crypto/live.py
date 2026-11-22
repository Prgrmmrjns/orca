"""Live PAPER trading engine for strategy v3 on Kraken public prices.

  python -m crypto.live tick      # mark to market; rebalance once per completed UTC day
  python -m crypto.live status    # print the book

No API keys, no orders: fills are simulated at Kraken's live bid (sells) and
ask (buys). Commission is FEE (default 0, per the user's zero-fee assumption).
State lives in state/crypto/ledger.json and is committed to git by whoever runs
the tick (scheduled agent or GitHub Action).
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, time
from pathlib import Path
import numpy as np, pandas as pd
from . import kraken as K, strategy as S
from .model import JsonModel

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "crypto"
LEDGER, SIGNALS, OVERRIDES = STATE / "ledger.json", STATE / "signals.json", STATE / "overrides.json"
START_EUR = 1000.0
FEE = float(os.environ.get("ORCA_CRYPTO_FEE", "0"))   # commission per side
CANDIDATES = 60          # fetch daily history for the top-N EUR pairs by 24h volume
MIN_BARS = 210           # need 200d SMA
MIN_TRADE_EUR = 5.0

def now(): return dt.datetime.now(dt.timezone.utc)
def iso(t=None): return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_ledger():
    if LEDGER.exists(): return json.loads(LEDGER.read_text())
    return dict(strategy="orca_crypto_v3", mode="paper", currency="EUR", start=iso(), start_equity=START_EUR,
                cash=START_EUR, positions={}, last_signal_date=None, trades=[], history=[])

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float) + "\n")

def load_overrides(today: str) -> dict:
    """Risk-reduction-only caps: {"SOL": {"max_weight": 0.0, "until": "2026-12-01", "why": "..."}}"""
    if not OVERRIDES.exists(): return {}
    return {c: o for c, o in json.loads(OVERRIDES.read_text()).items()
            if not c.startswith("_") and o.get("until", "9999") >= today}

def build_panels(pairs, tick, held):
    ranked = sorted(tick, key=lambda c: -tick[c]["vol_eur_24h"])[:CANDIDATES]
    coins = list(dict.fromkeys(["BTC", *held, *ranked]))
    bars = {}
    for c in coins:
        try: df = K.daily_ohlc(pairs[c]["pair"])
        except Exception as e:
            print(f"warn: no OHLC for {c}: {e}", file=sys.stderr); continue
        if len(df) >= MIN_BARS or c in held: bars[c] = df
        time.sleep(0.35)
    idx = pd.date_range(bars["BTC"].index[0], bars["BTC"].index[-1], freq="D")
    P = {f: pd.DataFrame({c: b[f] for c, b in bars.items()}).reindex(idx) for f in ("close", "high", "low", "qvol")}
    P["qvol"] = P["qvol"].fillna(0.0).where(P["close"].ffill().notna())
    for f in ("close", "high", "low"): P[f] = P[f].ffill()
    # drop pegged assets the name list missed (annualised 30d vol < 15%)
    rv = P["close"].pct_change().iloc[-30:].std() * np.sqrt(365)
    keep = [c for c in P["close"].columns if c == "BTC" or c in held or rv[c] > 0.15]
    return {f: v[keep] for f, v in P.items()}

def compute_signal(P, model):
    p = S.PARAMS
    close, qvol = P["close"], P["qvol"]
    un = S.universe(close, qvol, p["universe_n"], p["min_history"])
    last = close.index[-1:]
    X = S.feature_frame(close, P["high"], P["low"], qvol, un, rows=last)
    pred = pd.Series(model.predict(X), index=X.index).unstack().reindex(index=close.index, columns=close.columns)
    w, gate = S.target_weights(close, qvol, pred, p)
    d = last[0]
    mom = S.momentum_sleeve(close, un, p).loc[d]; ml = S.ml_sleeve(pred, p).loc[d]
    btc = close["BTC"]
    info = dict(signal_date=str(d.date()), gate_on=bool(gate.loc[d] > 0), btc_close_eur=float(btc.loc[d]),
                btc_sma100_eur=float(btc.rolling(p["gate_sma"]).mean().loc[d]),
                universe=sorted(un.columns[un.loc[d]]),
                momentum_picks=sorted(mom.index[mom > 0]), ml_picks=sorted(ml.index[ml > 0]),
                forecast_7d={c: round(float(v), 4) for c, v in pred.loc[d].dropna().sort_values(ascending=False).items()},
                target_weights={c: round(float(v), 4) for c, v in w.loc[d].items() if v > 0})
    return w.loc[d], info

def equity(led, tick):
    return led["cash"] + sum(q * tick[c]["bid"] for c, q in led["positions"].items() if c in tick)

def rebalance(led, target: pd.Series, tick, pairs, signal_date):
    eq = equity(led, tick); band = S.PARAMS["band"]; ts = iso(); orders = []
    for c in sorted(set(led["positions"]) | set(target.index[target > 0])):
        if c not in tick: continue
        mid = (tick[c]["bid"] + tick[c]["ask"]) / 2
        cur = led["positions"].get(c, 0.0) * mid
        tgt = float(target.get(c, 0.0)) * eq
        delta = tgt - cur
        if abs(delta) < MIN_TRADE_EUR: continue
        if tgt > 0 and abs(delta) / eq <= band: continue          # inside the no-trade band
        orders.append((c, delta, tgt))
    for c, delta, tgt in sorted(orders, key=lambda o: o[1]):       # sells first to free cash
        if delta < 0:
            px = tick[c]["bid"]; held = led["positions"][c]
            qty = held if tgt == 0 else min(held, -delta / px)
            led["cash"] += qty * px * (1 - FEE)
            left = held - qty
            if left * px < 1e-6: led["positions"].pop(c)
            else: led["positions"][c] = left
        else:
            px = tick[c]["ask"]; spend = min(delta, led["cash"] / (1 + FEE))
            if spend < MIN_TRADE_EUR: continue
            m = 10 ** pairs[c]["lot_decimals"]; qty = int(spend / px * m) / m   # round down to lot size
            if qty < pairs[c]["ordermin"]: continue
            led["cash"] -= qty * px * (1 + FEE)
            led["positions"][c] = led["positions"].get(c, 0.0) + qty
        led["trades"].append(dict(ts=ts, signal_date=signal_date, coin=c, side="sell" if delta < 0 else "buy",
                                  qty=qty, price=px, eur=round(qty * px, 2), fee=round(qty * px * FEE, 4),
                                  spread_bps=round((tick[c]["ask"] - tick[c]["bid"]) / px * 1e4, 1)))
    return len(orders)

def mark(led, tick):
    eq = equity(led, tick); ts = iso()
    pos = {c: round(q * tick[c]["bid"], 2) for c, q in led["positions"].items() if c in tick}
    point = dict(ts=ts, equity=round(eq, 2), cash=round(led["cash"], 2), positions=pos)
    h = led["history"]
    if h and h[-1]["ts"][:13] == ts[:13]: h[-1] = point      # at most one point per hour
    else: h.append(point)
    return point

def tick(force=False):
    led = load_ledger()
    pairs = K.eur_pairs(); tk = K.tickers(pairs)
    today = str(now().date()); done = False
    P = None
    if force or led["last_signal_date"] != str((now() - dt.timedelta(days=1)).date()):
        P = build_panels(pairs, tk, list(led["positions"]))
        target, info = compute_signal(P, JsonModel())
        if force or info["signal_date"] != led["last_signal_date"]:
            ov = load_overrides(today)
            for c, o in ov.items():
                if c in target.index and target[c] > o.get("max_weight", 0.0):
                    target[c] = float(o.get("max_weight", 0.0)); info.setdefault("overrides_applied", {})[c] = o
            tk = K.tickers(pairs)                       # fresh quotes right before filling
            n = rebalance(led, target, tk, pairs, info["signal_date"])
            led["last_signal_date"] = info["signal_date"]
            info.update(computed_at=iso(), orders=n)
            save(SIGNALS, info); done = True
    point = mark(led, tk)
    save(LEDGER, led)
    return dict(rebalanced=done, **point, pnl_pct=round((point["equity"] / led["start_equity"] - 1) * 100, 2))

def status():
    led = load_ledger(); h = led["history"]
    out = dict(start=led["start"], last_signal_date=led["last_signal_date"], cash=round(led["cash"], 2),
               positions=led["positions"], last=h[-1] if h else None, n_trades=len(led["trades"]))
    if h:
        eq = pd.Series({x["ts"]: x["equity"] for x in h})
        out.update(pnl_pct=round((eq.iloc[-1] / led["start_equity"] - 1) * 100, 2),
                   max_drawdown_pct=round(float((eq / eq.cummax() - 1).min()) * 100, 2))
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["tick", "status"]); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(json.dumps(tick(a.force) if a.cmd == "tick" else status(), indent=1, default=float))
