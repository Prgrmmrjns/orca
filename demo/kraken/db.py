"""Thin PostgREST client for the orca-agent Supabase database (RLS: every request needs the x-orca-key header)."""
from __future__ import annotations

import json
import os
from typing import Any

import requests

_S = requests.Session()


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/"


def _h(extra: dict | None = None) -> dict:
    k = os.environ["SUPABASE_ANON_KEY"]
    h = {"apikey": k, "x-orca-key": os.environ["ORCA_DB_KEY"], "Content-Type": "application/json"}
    if not k.startswith("sb_"):
        h["Authorization"] = f"Bearer {k}"
    if extra:
        h.update(extra)
    return h


def _check(r: requests.Response):
    if r.status_code >= 300:
        raise RuntimeError(f"db {r.request.method} {r.url[:160]} -> {r.status_code}: {r.text[:300]}")
    return r


def select(table: str, params: dict | None = None, limit: int | None = None) -> list[dict]:
    p = dict(params or {})
    p.setdefault("select", "*")
    if limit:
        p["limit"] = str(limit)
    out, off = [], 0
    while True:
        rng = {"Range-Unit": "items", "Range": f"{off}-{off + 999}"}
        r = _check(_S.get(_base() + table, params=p, headers=_h(rng), timeout=30))
        rows = r.json()
        out += rows
        if limit or len(rows) < 1000:
            return out[:limit] if limit else out
        off += 1000


def insert(table: str, rows: list[dict] | dict, upsert: bool = False, on_conflict: str | None = None,
           returning: bool = False, ignore_dupes: bool = False) -> list[dict]:
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return []
    prefer = []
    if upsert:
        prefer.append("resolution=merge-duplicates")
    elif ignore_dupes:
        prefer.append("resolution=ignore-duplicates")
    prefer.append("return=representation" if returning else "return=minimal")
    params = {"on_conflict": on_conflict} if on_conflict else None
    out = []
    for i in range(0, len(rows), 1000):
        r = _check(_S.post(_base() + table, params=params, data=json.dumps(rows[i:i + 1000], default=str),
                           headers=_h({"Prefer": ",".join(prefer)}), timeout=60))
        if returning:
            out += r.json()
    return out


def update(table: str, match: dict, values: dict) -> None:
    params = {k: f"eq.{v}" for k, v in match.items()}
    _check(_S.patch(_base() + table, params=params, data=json.dumps(values, default=str),
                    headers=_h({"Prefer": "return=minimal"}), timeout=30))


def delete(table: str, match: dict) -> None:
    params = {k: (v if isinstance(v, str) and "." in v[:6] else f"eq.{v}") for k, v in match.items()}
    _check(_S.delete(_base() + table, params=params, headers=_h(), timeout=30))


def kv_get(k: str, default: Any = None) -> Any:
    rows = select("kv", {"k": f"eq.{k}"})
    return rows[0]["v"] if rows else default


def kv_set(k: str, v: Any) -> None:
    from datetime import datetime, timezone
    insert("kv", {"k": k, "v": v, "updated": datetime.now(timezone.utc).isoformat()}, upsert=True)


def rpc(fn: str, args: dict) -> Any:
    r = _check(_S.post(_base() + "rpc/" + fn, data=json.dumps(args, default=str), headers=_h(), timeout=60))
    return r.json()
