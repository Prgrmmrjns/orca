"""Prompt lab: runs a batch of structured-output calls server-side (where the API key lives).
Used offline to evaluate prompt variants against realised price moves. Separate spend cap."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import db, llm

PRICES = {"claude-haiku-5-5": (0.10e-6, 0.50e-6), "claude-sonnet-5-5": (3e-6, 15e-6), "claude-opus-5-5": (5e-6, 25e-6)}
LAB_CAP = 90.0


def run(model: str, system: str, users: list[str], schema: dict, max_tokens: int = 4000, effort: str = "low",
        workers: int = 16) -> dict:
    spent = (db.kv_get("lab_spend", {}) or {}).get("usd", 0.0)
    if spent >= LAB_CAP:
        return {"error": f"lab cap ${LAB_CAP} reached", "spent": spent}
    pin, pout = PRICES[model]

    def one(u):
        try:
            r = llm.client().messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": u}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
            txt = next((b.text for b in r.content if b.type == "text"), None)
            return {"out": json.loads(txt) if txt else None, "usd": r.usage.input_tokens * pin + r.usage.output_tokens * pout}
        except Exception as e:  # report, keep the batch going
            return {"out": None, "usd": 0.0, "err": str(e)[:300]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(one, users))
    usd = sum(r["usd"] for r in res)
    s = db.kv_get("lab_spend", {}) or {}
    s["usd"] = s.get("usd", 0.0) + usd
    s[model] = s.get(model, 0.0) + usd
    db.kv_set("lab_spend", s)
    return {"results": res, "usd": usd, "spent": s["usd"]}
