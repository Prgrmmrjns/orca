"""Always-on loop: poll sources -> log every new item with our own first-seen
time and live perp quotes -> classify -> paper-trade -> manage exits.

State (committed to git by the runner):
  state/newsperp/events/YYYY-MM-DD.jsonl   one line per new item
  state/newsperp/ledger.json               paper book
  state/newsperp/seen.json                 item ids already processed, per source
  state/newsperp/health.json               per-source poll stats
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import market
from .book import Book, iso, new_ledger, now_utc, parse
from .classify import Classifier
from .sources import Poller, fetch_text, match_tickers, sec_exhibit_text

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "newsperp"
STATE = ROOT / "state" / "newsperp"
SEEN_CAP = 400

def load_json(p: Path, default):
    try: return json.loads(p.read_text())
    except Exception: return default

def save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str) + "\n")
    tmp.replace(p)

def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)

class Collector:
    def __init__(self, sources_cfg=None, book_cfg=None, state_dir: Path = STATE):
        self.scfg = sources_cfg or load_json(CFG / "sources.json", {})
        self.bcfg = book_cfg or load_json(CFG / "book.json", {})
        self.state = state_dir
        self.universe = self.scfg["universe"]
        self.perps = [u["perp"] for u in self.universe.values() if u.get("perp")]
        self.pollers = [Poller(s, self.universe) for s in self.scfg["sources"]]
        self.seen = load_json(self.state / "seen.json", {})
        self.ledger = load_json(self.state / "ledger.json", None) or new_ledger(self.bcfg)
        self.book = Book(self.ledger, self.bcfg, self.universe)
        tradeable = [s for s, u in self.universe.items() if u.get("perp")]
        self.clf = Classifier(tradeable, self.bcfg.get("classifier_model", "claude-opus-5-5"),
                              self.bcfg.get("classifier_effort", "low"))
        self.fx = None; self.fx_ts = 0.0
        self.n_events = self.n_trades = 0
        self.dirty = False

    # ---- helpers ----------------------------------------------------------
    def eurusd(self):
        if self.fx is None or time.time() - self.fx_ts > 600:
            try: self.fx, self.fx_ts = market.eurusd(), time.time()
            except Exception as e: log("eurusd failed", e)
        return self.fx or 1.1

    def seen_key(self, item): return f"{item['source']}:{item['tickers'][0]}" if item["source"] == "sec_8k" else item["source"]

    def write_event(self, ev: dict):
        p = self.state / "events" / f"{ev['first_seen'][:10]}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f: f.write(json.dumps(ev, default=str) + "\n")

    # ---- item pipeline ----------------------------------------------------
    def handle_items(self, poller: Poller, items: list[dict]):
        fresh, baseline = [], set()
        for it in items:
            if not it.get("id"): continue
            k = self.seen_key(it)
            if k not in self.seen:                 # first poll of this source ever: record ids, emit nothing
                self.seen[k] = []; baseline.add(k)
            if it["id"] in self.seen[k]: continue
            self.seen[k].append(it["id"]); self.dirty = True
            if k not in baseline: fresh.append(it)
        for k in {self.seen_key(it) for it in items if it.get("id")}:
            self.seen[k] = self.seen[k][-SEEN_CAP:]
        if baseline: log(f"baseline {sorted(baseline)}: {len(items)} existing items recorded, not traded")
        if not fresh: return
        try: ticks = market.tickers(self.perps)
        except Exception as e:
            log("tickers failed", e); ticks = {}
        fx = self.eurusd()
        for it in fresh: self.process(poller, it, ticks, fx)

    def process(self, poller: Poller, it: dict, ticks: dict, fx: float):
        t_seen = now_utc()
        text = f"{it['title']} {it.get('summary', '')}"
        tickers = sorted(set(it.get("tickers") or []) | set(match_tickers(text, self.universe)))
        it["tickers"] = tickers
        pub = parse(it["published"]) if it.get("published") else None
        lag = (t_seen - pub).total_seconds() if pub else None
        stale = lag is not None and lag > self.bcfg["max_item_age_minutes"] * 60
        mode = poller.src.get("llm", "match")
        want = mode == "all" or (mode == "match" and tickers)
        ev = dict(key=f"{it['source']}|{it['id']}", source=it["source"], first_seen=iso(t_seen), published=it.get("published"),
                  lag_s=round(lag, 1) if lag is not None else None, stale=stale, title=it["title"], url=it.get("url"),
                  summary=it.get("summary", "")[:500], tickers=tickers,
                  quotes={s: round((ticks[u['perp']]['bid'] + ticks[u['perp']]['ask']) / 2, 4)
                          for s, u in self.universe.items() if u.get("perp") in ticks})
        if want and not stale:
            body = ""
            if it["source"] == "sec_8k": body = sec_exhibit_text(it)
            elif len(it.get("summary", "")) < 300 and it.get("url") and mode == "all": body = fetch_text(it["url"])
            sig = self.clf.classify(it, body)
            ev.update(event_type=sig["event_type"], signal=sig, classified_s=round((now_utc() - t_seen).total_seconds(), 2))
            if sig["impacts"]:
                tk2 = ticks
                try: tk2 = market.tickers(self.perps)         # fresh quote after our classification delay
                except Exception: pass
                ev["decisions"] = []
                for imp in sig["impacts"]:
                    res = self.book.try_open(ev, imp, tk2, fx)
                    ev["decisions"].append({imp["symbol"]: res.get("skipped") or "opened"})
                    if "opened" in res:
                        self.n_trades += 1; self.dirty = True
                        log(f"OPEN {res['opened']['side']} {imp['symbol']} {res['opened']['notional_eur']}EUR <- {it['title'][:80]}")
        self.write_event(ev); self.n_events += 1; self.dirty = True
        log(f"[{it['source']}] lag={ev['lag_s']}s {tickers} {it['title'][:90]}"
            + (f" -> {ev.get('event_type')} {[(i['symbol'], i['direction'], i['confidence']) for i in ev.get('signal', {}).get('impacts', [])]}" if ev.get("signal") else ""))

    # ---- book housekeeping ------------------------------------------------
    def manage_book(self, full_mark=False):
        fx = self.eurusd()
        try:
            if full_mark or not self.ledger["positions"]:
                ticks = market.tickers(self.perps)
            else:
                ticks = {p["perp"]: market.ticker(p["perp"]) for p in self.ledger["positions"].values()}
        except Exception as e:
            log("book quotes failed", e); return
        for tr in self.book.update(ticks, fx):
            log(f"CLOSE {tr['symbol']} {tr['why']} pnl {tr['pnl_eur']}EUR ({tr['ret_pct']}%)"); self.dirty = True
        if full_mark:
            self.book.mark(ticks, fx); self.dirty = True

    def save(self):
        save_json(self.state / "ledger.json", self.ledger)
        save_json(self.state / "seen.json", self.seen)
        save_json(self.state / "health.json", dict(updated=iso(now_utc()), classifier=self.clf.method,
                                                   llm_calls=self.clf.calls, llm_failures=self.clf.failures,
                                                   sources={p.src["id"]: p.stats for p in self.pollers}))
        self.dirty = False

    def git_commit(self):
        cmds = [["git", "add", "state/newsperp"],
                ["git", "commit", "-q", "-m", f"newsperp: collector {iso(now_utc())}"],
                ["git", "pull", "-q", "--rebase", "--autostash", "origin", os.environ.get("NEWSPERP_BRANCH", "main")],
                ["git", "push", "-q", "origin", f"HEAD:{os.environ.get('NEWSPERP_BRANCH', 'main')}"]]
        for c in cmds:
            r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True)
            if r.returncode and c[1] != "commit":
                log("git", " ".join(c[1:3]), "failed:", (r.stderr or r.stdout)[:200]); break

    # ---- main loop --------------------------------------------------------
    def run(self, minutes: float = 110, commit_every: float = 0, book_every: float = 10, mark_every: float = 300):
        end = time.time() + minutes * 60
        last_book = last_mark = last_save = last_commit = 0.0
        log(f"collector start: {len(self.pollers)} sources, classifier={self.clf.method}, open={list(self.ledger['positions'])}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            while time.time() < end:
                now = time.time()
                due = [p for p in self.pollers if p.next_due <= now]
                for p in due: p.next_due = now + p.backoff()
                for p, items in zip(due, pool.map(lambda p: p.poll(), due)):
                    if p.errors == 1: log(f"source {p.src['id']} error: {p.stats['last_error']}")
                    if items: self.handle_items(p, items)
                if now - last_mark >= mark_every:
                    self.manage_book(full_mark=True); last_mark = last_book = now
                elif self.ledger["positions"] and now - last_book >= book_every:
                    self.manage_book(); last_book = now
                if self.dirty and now - last_save >= 30:
                    self.save(); last_save = now
                if commit_every and now - last_commit >= commit_every * 60:
                    self.save(); self.git_commit(); last_commit = now
                time.sleep(max(0.2, min([p.next_due for p in self.pollers] + [now + 1]) - time.time()))
        self.manage_book(full_mark=True)
        self.save()
        if commit_every: self.git_commit()
        log(f"collector stop: {self.n_events} events, {self.n_trades} trades, equity {self.ledger['history'][-1]['equity_eur'] if self.ledger['history'] else '-'} EUR")
