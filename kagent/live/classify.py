"""Haiku headline classifier: one cheap call per new headline -> structured trade signal (paper only)."""
from __future__ import annotations

import time
from typing import List, Literal, Optional

from pydantic import BaseModel

MODEL = "claude-haiku-5-5"  # cheap model, as Jonas asked

SYSTEM = """You read one breaking headline (plus a short excerpt) and judge its likely price impact in the next
1-24 hours on assets tradable as Kraken perpetual futures. Tradable: crypto (BTC, ETH, SOL, XRP, ...), US stocks
via xStocks perps (NVDA, TSLA, AAPL, GOOGL, META, AMZN, COIN, MSTR, HOOD, CRCL), SPY, QQQ, US100, gold, silver, oil,
EURUSD. Only name an asset if the headline is clearly about it or clearly moves it (e.g. Fed decisions move SPY,
QQQ, BTC, gold). Most headlines are noise: when unsure, set direction to "none". Do not trade on old news, opinion
pieces, ads, or routine filings."""


class Signal(BaseModel):
    assets: List[str]
    direction: Literal["long", "short", "none"]
    event_type: Literal["earnings", "guidance", "macro_fed", "macro_data", "m_and_a", "regulatory", "legal_scandal",
                        "exec_change", "product", "hack_exploit", "listing", "other"]
    surprise: Literal["high", "medium", "low"]
    confidence: float
    reason: str


def classify(client, title: str, summary: str = "", source: str = "") -> tuple[Optional[Signal], float, dict]:
    """Returns (signal or None on refusal, seconds taken, token usage)."""
    t = time.time()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=400,
        system=SYSTEM,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": f"Source: {source}\nHeadline: {title}\nExcerpt: {summary[:800]}"}],
        output_format=Signal,
    )
    dt = time.time() - t
    usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    if resp.stop_reason == "refusal":
        return None, dt, usage
    return resp.parsed_output, dt, usage
