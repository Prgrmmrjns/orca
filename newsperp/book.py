"""Paper perpetual-futures book in EUR, filled at live Kraken bid/ask.

Positions are USD-margined linear perps (PnL in USD, converted to EUR at the
current Kraken EUR/USD). Costs: taker fee on notional per fill, extra
slippage on every fill, hourly funding accrued continuously.
"""
from __future__ import annotations

import datetime as dt
import math

def now_utc(): return dt.datetime.now(dt.timezone.utc)
def iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def new_ledger(cfg: dict, t=None) -> dict:
    return dict(strategy="newsperp_v0", mode="paper", currency="EUR", start=iso(t or now_utc()),
                start_equity_eur=cfg["start_equity_eur"], cash_eur=cfg["start_equity_eur"],
                positions={}, trades=[], history=[], day_start={}, halted_days=[])

class Book:
    def __init__(self, ledger: dict, cfg: dict, universe: dict):
        self.L, self.cfg, self.universe = ledger, cfg, universe

    # ---- valuation --------------------------------------------------------
    def unrealized_usd(self, ticks: dict) -> float:
        u = 0.0
        for sym, p in self.L["positions"].items():
            t = ticks.get(p["perp"])
            if not t: continue
            exit_px = t["bid"] if p["side"] == "long" else t["ask"]
            u += self._pnl_usd(p, exit_px) - p.get("funding_usd", 0.0)
        return u

    @staticmethod
    def _pnl_usd(p, px):
        return (px - p["entry_px"]) * p["qty"] * (1 if p["side"] == "long" else -1)

    def equity_eur(self, ticks: dict, fx: float) -> float:
        return self.L["cash_eur"] + self.unrealized_usd(ticks) / fx

    def gross_eur(self, ticks: dict, fx: float) -> float:
        return sum(p["qty"] * ticks.get(p["perp"], {}).get("mark", p["entry_px"]) for p in self.L["positions"].values()) / fx

    # ---- entries ----------------------------------------------------------
    def stop_pct(self, sym):
        s = self.cfg["stop_pct"]
        return float(s.get(sym, s["default"]))

    def try_open(self, event: dict, imp: dict, ticks: dict, fx: float, t=None) -> dict:
        """Returns {"opened": trade} or {"skipped": reason}."""
        t = t or now_utc(); c = self.cfg; sym = imp["symbol"]
        u = self.universe.get(sym) or {}
        perp = u.get("perp")
        if not perp: return {"skipped": "no perp for symbol"}
        if imp["direction"] not in ("long", "short"): return {"skipped": "no direction"}
        if imp["confidence"] < c["min_confidence"]: return {"skipped": f"confidence {imp['confidence']:.2f} < {c['min_confidence']}"}
        if abs(imp.get("expected_move_pct", 0)) < c["min_expected_move_pct"]: return {"skipped": "expected move too small"}
        if event.get("stale"): return {"skipped": "item older than max_item_age_minutes"}
        day = t.strftime("%Y-%m-%d")
        if day in self.L["halted_days"]: return {"skipped": "daily loss limit hit"}
        tk = ticks.get(perp)
        if not tk or tk["suspended"] or tk["bid"] <= 0: return {"skipped": "no live quote"}
        pos = self.L["positions"].get(sym)
        if pos:
            if pos["side"] == imp["direction"]: return {"skipped": "already positioned"}
            self.close(sym, tk, fx, "reversed by new signal", t)
        if len(self.L["positions"]) >= c["max_open_positions"]: return {"skipped": "max open positions"}
        eq = self.equity_eur(ticks, fx)
        stop = self.stop_pct(sym) / 100
        notional_eur = min(c["risk_per_trade"] * eq / stop, c["max_position_frac"] * eq,
                           c["max_gross_leverage"] * eq - self.gross_eur(ticks, fx))
        if notional_eur < 10: return {"skipped": "no room under leverage cap"}
        slip = c["extra_slippage"]
        px = tk["ask"] * (1 + slip) if imp["direction"] == "long" else tk["bid"] * (1 - slip)
        qty = math.floor(notional_eur * fx / px * 1e4) / 1e4
        if qty <= 0: return {"skipped": "size rounds to zero"}
        fee_usd = qty * px * c["taker_fee"]
        self.L["cash_eur"] -= fee_usd / fx
        hold = int(min(max(imp.get("horizon_minutes") or c["default_hold_minutes"], 15), c["max_hold_minutes"]))
        stop_px = px * (1 - stop) if imp["direction"] == "long" else px * (1 + stop)
        self.L["positions"][sym] = dict(perp=perp, side=imp["direction"], qty=qty, entry_px=px, entry_ts=iso(t),
                                        stop_px=stop_px, exit_after=iso(t + dt.timedelta(minutes=hold)),
                                        event_id=event["key"], funding_usd=0.0, last_accrual=iso(t),
                                        confidence=imp["confidence"], reason=imp.get("reason", ""))
        tr = dict(ts=iso(t), symbol=sym, perp=perp, action="open", side=imp["direction"], qty=qty, price=round(px, 4),
                  notional_eur=round(qty * px / fx, 2), fee_eur=round(fee_usd / fx, 4), event_id=event["key"],
                  event_type=event.get("event_type"), confidence=imp["confidence"], hold_min=hold,
                  quote_bid=tk["bid"], quote_ask=tk["ask"], lag_s=event.get("lag_s"))
        self.L["trades"].append(tr)
        return {"opened": tr}

    # ---- exits ------------------------------------------------------------
    def close(self, sym, tk, fx, why, t=None) -> dict:
        t = t or now_utc(); p = self.L["positions"].pop(sym); slip = self.cfg["extra_slippage"]
        px = tk["bid"] * (1 - slip) if p["side"] == "long" else tk["ask"] * (1 + slip)
        fee_usd = p["qty"] * px * self.cfg["taker_fee"]
        pnl_usd = self._pnl_usd(p, px) - p.get("funding_usd", 0.0) - fee_usd
        self.L["cash_eur"] += pnl_usd / fx
        entry_fee = next((x["fee_eur"] for x in reversed(self.L["trades"]) if x["action"] == "open" and x["symbol"] == sym
                          and x["event_id"] == p["event_id"]), 0.0)
        tr = dict(ts=iso(t), symbol=sym, perp=p["perp"], action="close", side=p["side"], qty=p["qty"], price=round(px, 4),
                  entry_px=round(p["entry_px"], 4), why=why, funding_eur=round(p.get("funding_usd", 0.0) / fx, 4),
                  fee_eur=round(fee_usd / fx, 4), pnl_eur=round(pnl_usd / fx - entry_fee, 2),
                  ret_pct=round(self._pnl_usd(p, px) / (p["qty"] * p["entry_px"]) * 100, 2), event_id=p["event_id"],
                  held_min=round((t - parse(p["entry_ts"])).total_seconds() / 60, 1))
        self.L["trades"].append(tr)
        return tr

    def update(self, ticks: dict, fx: float, t=None) -> list[dict]:
        """Accrue funding, then apply stop / time exits. Also enforces the daily loss limit."""
        t = t or now_utc(); closed = []
        for sym in list(self.L["positions"]):
            p = self.L["positions"][sym]; tk = ticks.get(p["perp"])
            if not tk: continue
            hrs = (t - parse(p["last_accrual"])).total_seconds() / 3600
            sign = 1 if p["side"] == "long" else -1          # longs pay positive funding
            p["funding_usd"] = p.get("funding_usd", 0.0) + sign * p["qty"] * tk["mark"] * tk["funding_rel_per_hour"] * hrs
            p["last_accrual"] = iso(t)
            hit_stop = tk["bid"] <= p["stop_px"] if p["side"] == "long" else tk["ask"] >= p["stop_px"]
            if hit_stop: closed.append(self.close(sym, tk, fx, "stop", t))
            elif t >= parse(p["exit_after"]): closed.append(self.close(sym, tk, fx, "time", t))
        day = t.strftime("%Y-%m-%d")
        eq = self.equity_eur(ticks, fx)
        start = self.L["day_start"].setdefault(day, round(eq, 2))
        if day not in self.L["halted_days"] and eq < start * (1 - self.cfg["daily_loss_limit"]):
            self.L["halted_days"].append(day)
            for sym in list(self.L["positions"]):
                tk = ticks.get(self.L["positions"][sym]["perp"])
                if tk: closed.append(self.close(sym, tk, fx, "daily loss limit", t))
        return closed

    def mark(self, ticks: dict, fx: float, t=None, every_min: int = 5) -> dict:
        t = t or now_utc()
        pt = dict(ts=iso(t), equity_eur=round(self.equity_eur(ticks, fx), 2), cash_eur=round(self.L["cash_eur"], 2),
                  gross_eur=round(self.gross_eur(ticks, fx), 2), open=len(self.L["positions"]), eurusd=round(fx, 5))
        h = self.L["history"]
        bucket = lambda x: int(parse(x).timestamp()) // (every_min * 60)   # one point per bucket
        if h and bucket(h[-1]["ts"]) == bucket(pt["ts"]): h[-1] = pt
        else: h.append(pt)
        return pt
