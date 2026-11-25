"""Turn a news item into trade signals.

Uses Claude (structured output) when ANTHROPIC_API_KEY is set, otherwise a
small keyword rule set. Either way the result has the same shape:

  {"event_type", "summary", "method", "impacts": [{"symbol", "direction",
   "confidence", "expected_move_pct", "horizon_minutes", "reason"}]}
"""
from __future__ import annotations

import os
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["earnings", "guidance", "leadership", "m_and_a", "product", "regulatory", "legal",
                    "financing", "macro", "crypto", "political", "other"]

class Impact(BaseModel):
    symbol: str = Field(description="One of the tradeable symbols listed in the instructions")
    direction: Literal["long", "short", "none"]
    confidence: float = Field(description="0-1 probability the price moves in this direction over the horizon")
    expected_move_pct: float = Field(description="Expected absolute % move over the horizon")
    horizon_minutes: int = Field(description="How long the move should take to play out, 5-1440")
    reason: str = Field(description="One short sentence")

class Signal(BaseModel):
    event_type: EventType
    summary: str = Field(description="One sentence: what happened")
    is_new_information: bool = Field(description="False if this is a recap, opinion, ad, or old news")
    impacts: list[Impact] = Field(description="Empty if nothing tradeable")

SYSTEM = """You classify breaking news for a paper-trading research bot that trades perpetual futures.
Tradeable symbols: {symbols}.
(Stock symbols are tokenized-stock perps tracking the US share price; ANT and SPCX track pre-IPO Anthropic and SpaceX; SPY/QQQ are index trackers; GOLD is spot gold; the rest are crypto assets.)

For the item you get, decide whether it is genuinely new, market-moving information and, if so, which listed
symbols should move, in which direction, how much, and over what horizon. Rules:
- Only list a symbol when the item gives a concrete reason for it to move. Most items deserve no impact.
- Official releases (company newsroom, SEC 8-K, government) carry more weight than commentary or recaps.
- Earnings: you do not know analyst consensus. Judge from growth, margins, guidance changes and the company's own
  tone; keep confidence <= 0.6 unless guidance is clearly raised or cut or results are clearly extreme.
- Leadership changes, M&A, export controls, investigations and financing are often large; scheduled or widely
  expected events are usually priced in, so give them low confidence.
- confidence is your probability that the direction is right, not how important the news is.
Be terse."""

class Classifier:
    def __init__(self, symbols: list[str], model: str = "claude-opus-5-5", effort: str = "low"):
        self.symbols, self.model, self.effort = symbols, model, effort
        self.client = None
        self.calls = self.failures = 0
        if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("NEWSPERP_NO_LLM") != "1":
            import anthropic
            self.client = anthropic.Anthropic(max_retries=1, timeout=30.0)

    @property
    def method(self): return "llm" if self.client else "rules"

    def classify(self, item: dict, body: str = "") -> dict:
        if self.client:
            try:
                return self._llm(item, body)
            except Exception as e:  # API down / refusal / parse error -> rules, never crash the loop
                self.failures += 1
                out = rule_classify(item, body, self.symbols)
                out["llm_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                return out
        return rule_classify(item, body, self.symbols)

    def _llm(self, item: dict, body: str) -> dict:
        self.calls += 1
        t0 = time.time()
        text = (f"Source: {item['source']}\nPublished: {item.get('published')}\n"
                f"Pre-matched symbols: {', '.join(item.get('tickers') or []) or 'none'}\n"
                f"Title: {item['title']}\nSummary: {item.get('summary', '')}\n\nBody:\n{body[:6000]}")
        resp = self.client.beta.messages.parse(
            model=self.model, max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": self.effort},
            system=SYSTEM.format(symbols=", ".join(self.symbols)),
            messages=[{"role": "user", "content": text}],
            output_format=Signal,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"no parsed output (stop_reason={resp.stop_reason})")
        sig = resp.parsed_output
        impacts = [i.model_dump() for i in sig.impacts if i.symbol in self.symbols and i.direction != "none"]
        if not sig.is_new_information:
            impacts = []
        return dict(event_type=sig.event_type, summary=sig.summary, method="llm", model=resp.model,
                    latency_s=round(time.time() - t0, 2), new_info=sig.is_new_information, impacts=impacts,
                    usage=dict(inp=resp.usage.input_tokens, out=resp.usage.output_tokens))

# --------------------------------------------------------------------------
# Rule fallback: only a handful of unambiguous patterns, on pre-matched tickers.
RULES = [
    ("crypto_listing", r"\b(binance|coinbase|upbit|robinhood|kraken)\b.{0,60}\b(will list|lists|will add|adds|will launch|launches)\b", "long", 0.6, 3.0),
    ("crypto_delisting", r"\b(will delist|delisting|to delist)\b", "short", 0.6, 4.0),
    ("hack", r"\b(hacked|exploited|exploit|drained|stolen funds|security incident)\b", "short", 0.6, 4.0),
    ("leadership", r"\b(ceo|chief executive)\b.*\b(resign|steps? down|terminat|ousted|depart|leave)", "short", 0.6, 4.0),
    ("leadership", r"\b(resign|steps? down|ousted)\b.*\b(ceo|chief executive)\b", "short", 0.6, 4.0),
    ("leadership", r"\breinstat\w*\b.*\b(ceo|chief executive|president)\b", "long", 0.7, 5.0),
    ("guidance", r"\b(raises?|raised|increases?|boosts?)\b.{0,40}\b(guidance|outlook|forecast)\b", "long", 0.7, 3.0),
    ("guidance", r"\b(cuts?|lowers?|lowered|reduces?|withdraws?)\b.{0,40}\b(guidance|outlook|forecast)\b", "short", 0.7, 4.0),
    ("financing", r"\b(public offering|at-the-market|convertible (senior )?notes offering|share offering)\b", "short", 0.6, 2.5),
    ("legal", r"\b(sec charges|doj|indict|subpoena|investigation into)\b", "short", 0.6, 3.0),
    ("m_and_a", r"\b(to be acquired|agrees? to be acquired|definitive agreement to be acquired)\b", "long", 0.75, 8.0),
    ("regulatory", r"\bexport (controls?|restrictions?|ban)\b", "short", 0.55, 2.0),
]

def rule_classify(item: dict, body: str, symbols: list[str]) -> dict:
    text = f"{item['title']} {item.get('summary', '')} {body[:1500]}"
    tickers = [t for t in (item.get("tickers") or []) if t in symbols]
    for etype, pat, side, conf, move in RULES:
        if re.search(pat, text, re.I):
            return dict(event_type=etype, summary=item["title"], method="rules", new_info=True,
                        impacts=[dict(symbol=t, direction=side, confidence=conf, expected_move_pct=move,
                                      horizon_minutes=120, reason=f"rule:{etype}") for t in tickers])
    etype = "earnings" if re.search(r"\b(quarter|fiscal|results)\b", text, re.I) else "other"
    return dict(event_type=etype, summary=item["title"], method="rules", new_info=True, impacts=[])
