"""Live PAPER event listener: Kraken perp prices over WebSocket + news feeds + Haiku classifier.

Never sends orders. It only logs what it would do and marks paper P&L from live prices.
Run:  python -m kagent.live.run --minutes 60      (needs ANTHROPIC_API_KEY for the classifier)
Logs: research/kagent_live/events.jsonl, trades.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import websockets

from kagent.live.feeds import Poller

OUT = Path(__file__).resolve().parents[2] / "research" / "kagent_live"
FUT_WS = "wss://futures.kraken.com/ws/v1"
# what the classifier may name -> Kraken perp
PERPS = {"BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD", "XRP": "PF_XRPUSD", "DOGE": "PF_DOGEUSD",
         "NVDA": "PF_NVDAXUSD", "TSLA": "PF_TSLAXUSD", "AAPL": "PF_AAPLXUSD", "GOOGL": "PF_GOOGLXUSD",
         "META": "PF_METAXUSD", "AMZN": "PF_AMZNXUSD", "COIN": "PF_COINXUSD", "MSTR": "PF_MSTRXUSD",
         "HOOD": "PF_HOODXUSD", "CRCL": "PF_CRCLXUSD", "SPY": "PF_SPYXUSD", "QQQ": "PF_QQQXUSD",
         "US100": "PF_US100USD", "GOLD": "PF_XAUUSD", "SILVER": "PF_XAGUSD", "OIL": "PF_WTIOILUSD", "EUR": "PF_EURUSD"}
COST_SIDE = 0.0005 + 0.0010   # taker + event slippage
SIZE = 0.05                   # 5% of paper equity per signal
HOLD_S = 60 * 60              # exit after 1 hour
MIN_CONF = 0.7

prices: dict[str, tuple[float, float]] = {}  # perp -> (mark, recv_ts)


def log(name, rec):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{name}.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


async def price_feed():
    while True:
        try:
            async with websockets.connect(FUT_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({"event": "subscribe", "feed": "ticker", "product_ids": list(PERPS.values())}))
                async for m in ws:
                    d = json.loads(m)
                    if d.get("feed") == "ticker" and d.get("last"):
                        prices[d["product_id"]] = (float(d["last"]), time.time())
        except Exception as e:  # reconnect
            log("errors", {"t": time.time(), "where": "ws", "err": str(e)})
            await asyncio.sleep(3)


async def news_loop(minutes: float, classify_fn):
    poller, open_pos, end = Poller(), [], time.time() + minutes * 60
    while time.time() < end:
        items = await asyncio.to_thread(poller.poll)
        for it in items:
            sig, dt, usage = (None, 0.0, {})
            if classify_fn:
                try:
                    sig, dt, usage = await asyncio.to_thread(classify_fn, it["title"], it["summary"], it["source"])
                except Exception as e:
                    log("errors", {"t": time.time(), "where": "classify", "err": str(e)})
            rec = {**it, "classify_s": round(dt, 2), "usage": usage, "signal": sig.model_dump() if sig else None}
            log("events", rec)
            if sig and sig.direction != "none" and sig.confidence >= MIN_CONF:
                for a in sig.assets:
                    p = PERPS.get(a.upper())
                    if p and p in prices:
                        px = prices[p][0]
                        t = {"open_t": time.time(), "perp": p, "side": sig.direction, "entry": px, "size": SIZE,
                             "headline": it["title"], "reaction_s": round(time.time() - (it["published_ts"] or time.time()), 1)}
                        open_pos.append(t)
                        log("trades", {"action": "open", **t})
        for t in list(open_pos):
            if time.time() - t["open_t"] >= HOLD_S and t["perp"] in prices:
                px = prices[t["perp"]][0]
                r = (px / t["entry"] - 1) * (1 if t["side"] == "long" else -1) - 2 * COST_SIDE
                log("trades", {"action": "close", **t, "exit": px, "net_ret": round(r, 5)})
                open_pos.remove(t)
        await asyncio.sleep(1)


async def main(minutes: float):
    classify_fn = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        from kagent.live.classify import classify
        client = anthropic.Anthropic()
        classify_fn = lambda title, summary, source: classify(client, title, summary, source)
    else:
        print("No ANTHROPIC_API_KEY: logging news latency only, no classification or paper trades.")
    pf = asyncio.create_task(price_feed())
    await news_loop(minutes, classify_fn)
    pf.cancel()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    asyncio.run(main(ap.parse_args().minutes))
