"""Haiku calls with hard USD caps and usage logging (table llm_usage)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import db
from .config import HAIKU, LLM_CAP_DAILY, LLM_CAP_TOTAL, PRICE_IN, PRICE_OUT, SCRAPER_CAP_TOTAL

_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=2, timeout=60)
    return _client


def spend() -> dict:
    s = db.kv_get("llm_spend", {}) or {}
    today = datetime.now(timezone.utc).date().isoformat()
    if s.get("day") != today:
        s = {"day": today, "today": 0.0, "total": s.get("total", 0.0), "scraper_total": s.get("scraper_total", 0.0),
             "calls": s.get("calls", 0)}
    return s


def allowed(purpose: str) -> tuple[bool, str]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "no ANTHROPIC_API_KEY"
    s = spend()
    if s["total"] >= LLM_CAP_TOTAL:
        return False, f"total LLM cap ${LLM_CAP_TOTAL} reached"
    if s["today"] >= LLM_CAP_DAILY:
        return False, f"daily LLM cap ${LLM_CAP_DAILY} reached"
    if purpose == "classify" and s["scraper_total"] >= SCRAPER_CAP_TOTAL:
        return False, f"scraper cap ${SCRAPER_CAP_TOTAL} reached"
    return True, ""


def _record(purpose, usage):
    usd = usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT
    s = spend()
    s["today"] += usd
    s["total"] += usd
    s["calls"] = s.get("calls", 0) + 1
    if purpose == "classify":
        s["scraper_total"] += usd
    db.kv_set("llm_spend", s)
    db.insert("llm_usage", dict(purpose=purpose, model=HAIKU, in_tok=usage.input_tokens, out_tok=usage.output_tokens, usd=usd))
    return usd


def ask_json(purpose: str, system: str, user: str, schema: dict, max_tokens: int = 4000, effort: str = "low") -> dict | None:
    """One Haiku call constrained to a JSON schema. Returns parsed dict, or None if over budget / refused."""
    ok, why = allowed(purpose)
    if not ok:
        raise BudgetError(why)
    r = client().messages.create(
        model=HAIKU, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    _record(purpose, r.usage)
    if r.stop_reason == "refusal":
        return None
    txt = next((b.text for b in r.content if b.type == "text"), None)
    return json.loads(txt) if txt else None


class BudgetError(RuntimeError):
    pass
