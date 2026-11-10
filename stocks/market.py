"""Market helpers: session clock, FX, prices, Nasdaq calendar, EDGAR 8-K."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from stocks import EDGAR_UA
from stocks.paths import CACHE, ensure_dirs

ET = ZoneInfo("America/New_York")
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OrcaPaper/1.0; +https://github.com/Prgrmmrjns/orca)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
EDGAR_HEADERS = {
    "User-Agent": EDGAR_UA,
    "Accept-Encoding": "gzip, deflate",
}
SESSION = requests.Session()
SESSION.headers.update(EDGAR_HEADERS)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            t = data.strip()
            if t:
                self.parts.append(t)


def html_to_text(html: str) -> str:
    p = TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", " ".join(p.parts))


def now_et() -> datetime:
    return datetime.now(ET)


def is_weekday(d: date | None = None) -> bool:
    d = d or now_et().date()
    return d.weekday() < 5


def market_session(when: datetime | None = None) -> str:
    """Return 'pre', 'open', 'after', or 'closed'."""
    when = when or now_et()
    if when.weekday() >= 5:
        return "closed"
    t = when.hour * 60 + when.minute
    if t < 4 * 60:
        return "closed"
    if t < 9 * 60 + 30:
        return "pre"
    if t < 16 * 60:
        return "open"
    if t < 20 * 60:
        return "after"
    return "closed"


def parse_num(x) -> float | None:
    if x is None:
        return None
    s = str(x).replace("$", "").replace(",", "").replace("%", "").strip()
    if s in {"", "--", "N/A", "n/a", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


_MISSING = object()


def retry(fn, attempts: int = 3, delay: float = 1.2, default=_MISSING):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    if default is _MISSING:
        if last:
            raise last
        return None
    return default


def eurusd() -> float:
    """EURUSD rate (USD per 1 EUR). Fallback 1.08."""
    def _fetch():
        t = yf.Ticker("EURUSD=X")
        h = t.history(period="5d")
        if h is not None and not h.empty:
            return float(h["Close"].iloc[-1])
        info = t.fast_info
        v = getattr(info, "last_price", None) or getattr(info, "lastPrice", None)
        if v:
            return float(v)
        raise RuntimeError("no EURUSD")

    try:
        return float(retry(_fetch, attempts=3, default=1.08) or 1.08)
    except Exception:
        return 1.08


def spy_above_ma50() -> bool | None:
    try:
        h = yf.Ticker("SPY").history(period="4mo")
        if h is None or len(h) < 50:
            return None
        ma = h["Close"].rolling(50).mean().iloc[-1]
        return bool(h["Close"].iloc[-1] > ma)
    except Exception:
        return None


def spy_history_eur(start: str = "2026-11-10") -> list[dict]:
    """Daily SPY close converted to EUR index starting at 1000."""
    try:
        spy = yf.Ticker("SPY").history(start=start, auto_adjust=True)
        fx = yf.Ticker("EURUSD=X").history(start=start, auto_adjust=True)
        if spy is None or spy.empty:
            return []
        rows = []
        start_px = None
        start_fx = None
        for ts, row in spy.iterrows():
            d = ts.tz_convert(ET).date().isoformat() if getattr(ts, "tzinfo", None) else ts.date().isoformat()
            px = float(row["Close"])
            try:
                fx_row = fx.loc[fx.index.normalize() == ts.normalize()]
                rate = float(fx_row["Close"].iloc[-1]) if len(fx_row) else None
            except Exception:
                rate = None
            if rate is None:
                # nearest prior
                try:
                    prior = fx[fx.index <= ts]
                    rate = float(prior["Close"].iloc[-1]) if len(prior) else eurusd()
                except Exception:
                    rate = eurusd()
            if start_px is None:
                start_px, start_fx = px, rate
            # SPY in EUR terms relative to start equity 1000
            spy_eur = 1000.0 * (px / start_px) * (start_fx / rate)
            rows.append({"day": d, "spy_eur": round(spy_eur, 2), "spy_usd": round(px, 4), "eur_usd": round(rate, 5)})
        return rows
    except Exception:
        return []


def _nasdaq_json(day: date) -> dict | None:
    """Fetch Nasdaq earnings calendar JSON; curl + disk cache fallbacks."""
    import subprocess

    ensure_dirs()
    cache_path = CACHE / "nasdaq" / f"{day.isoformat()}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}"

    def _save(js: dict) -> dict:
        try:
            cache_path.write_text(json.dumps(js))
        except Exception:
            pass
        return js

    def _load_cache() -> dict | None:
        if cache_path.exists() and cache_path.stat().st_size > 50:
            try:
                return json.loads(cache_path.read_text())
            except Exception:
                return None
        return None

    # Prefer curl (HTTP/1.1) — HTTP/2 often aborts with INTERNAL_ERROR from this host.
    # Keep timeouts short so we fall back to disk cache quickly in flaky networks.
    for attempt in range(2):
        try:
            proc = subprocess.run(
                [
                    "curl", "-sS", "-m", "20", "--http1.1", "--compressed",
                    "-A", NASDAQ_HEADERS["User-Agent"],
                    "-H", f"Origin: {NASDAQ_HEADERS['Origin']}",
                    "-H", f"Referer: {NASDAQ_HEADERS['Referer']}",
                    "-H", f"Accept: {NASDAQ_HEADERS['Accept']}",
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout and len(proc.stdout) > 50:
                return _save(json.loads(proc.stdout))
        except Exception:
            pass
        time.sleep(0.5 + attempt)

    def _via_requests():
        r = requests.get(url, headers=NASDAQ_HEADERS, timeout=12)
        if r.status_code == 429:
            raise RuntimeError("rate limited")
        r.raise_for_status()
        return r.json()

    js = retry(_via_requests, attempts=2, delay=1.0, default=None)
    if js:
        return _save(js)

    cached = _load_cache()
    if cached:
        return cached
    return None


def nasdaq_earnings(day: date) -> list[dict]:
    js = _nasdaq_json(day)
    if not js:
        raise RuntimeError(f"nasdaq calendar failed for {day}")
    recs = ((js.get("data") or {}).get("rows")) or []
    out = []
    for rec in recs:
        ticker = str(rec.get("symbol") or "").replace(".", "-").upper()
        if not ticker:
            continue
        out.append(
            {
                "cal_date": day.isoformat(),
                "ticker": ticker,
                "name": rec.get("name") or ticker,
                "time": rec.get("time") or "",
                "eps_reported": parse_num(rec.get("eps")),
                "eps_estimate": parse_num(rec.get("epsForecast")),
                "surprise_pct": parse_num(rec.get("surprise")),
                "fiscal_quarter_ending": rec.get("fiscalQuarterEnding"),
                "market_cap": rec.get("marketCap"),
                "source": "nasdaq",
            }
        )
    return out


def yf_earnings_surprise(ticker: str) -> dict[str, Any]:
    """Latest earnings row from yfinance (EPS actual/estimate/surprise)."""
    def _one():
        ed = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if ed is None or ed.empty:
            return {}
        df = ed.reset_index()
        date_col = df.columns[0]
        df = df.rename(columns={date_col: "earnings_dt"})
        rename = {}
        for c in df.columns:
            cl = str(c).lower()
            if cl in {"eps estimate", "epsestimate"}:
                rename[c] = "eps_estimate"
            elif cl in {"reported eps", "reportedeps"}:
                rename[c] = "eps_reported"
            elif "surprise" in cl:
                rename[c] = "surprise_pct"
        df = df.rename(columns=rename)
        df["earnings_dt"] = __import__("pandas").to_datetime(df["earnings_dt"], utc=True, errors="coerce")
        df = df.dropna(subset=["earnings_dt"]).sort_values("earnings_dt", ascending=False)
        row = df.iloc[0]
        surprise = row.get("surprise_pct")
        if surprise is None or (isinstance(surprise, float) and __import__("math").isnan(surprise)):
            est = row.get("eps_estimate")
            act = row.get("eps_reported")
            if est is not None and act is not None and float(est) != 0:
                surprise = (float(act) - float(est)) / abs(float(est)) * 100
        edt = row["earnings_dt"]
        if getattr(edt, "tzinfo", None):
            edt = edt.tz_convert(ET)
        return {
            "earnings_dt": edt.isoformat(),
            "earnings_date": edt.date().isoformat(),
            "hour_et": int(edt.hour),
            "eps_estimate": _f(row.get("eps_estimate")),
            "eps_reported": _f(row.get("eps_reported")),
            "surprise_pct": _f(surprise),
            "source": "yfinance",
        }

    try:
        return retry(_one, attempts=3, default={}) or {}
    except Exception:
        return {}


def _f(v):
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


def price_snapshot(ticker: str) -> dict[str, Any]:
    """Pre/regular prices for gap estimate."""
    def _one():
        t = yf.Ticker(ticker)
        h = t.history(period="10d", interval="1d", auto_adjust=True)
        info = {}
        try:
            fi = t.fast_info
            info = {
                "last": _f(getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)),
                "prev_close": _f(getattr(fi, "previous_close", None) or getattr(fi, "previousClose", None)),
                "open": _f(getattr(fi, "open", None)),
                "day_high": _f(getattr(fi, "day_high", None) or getattr(fi, "dayHigh", None)),
                "day_low": _f(getattr(fi, "day_low", None) or getattr(fi, "dayLow", None)),
            }
        except Exception:
            pass
        prev_close = info.get("prev_close")
        last_open = None
        last_close = None
        last_high = None
        last_low = None
        if h is not None and not h.empty:
            last_close = float(h["Close"].iloc[-1])
            last_open = float(h["Open"].iloc[-1])
            last_high = float(h["High"].iloc[-1])
            last_low = float(h["Low"].iloc[-1])
            if len(h) >= 2 and prev_close is None:
                prev_close = float(h["Close"].iloc[-2])
        last = info.get("last") or last_close
        open_px = info.get("open") or last_open
        gap = None
        ref = prev_close
        if ref and open_px:
            gap = (open_px / ref) - 1.0
        elif ref and last:
            gap = (last / ref) - 1.0
        return {
            "last": last,
            "open": open_px,
            "prev_close": prev_close,
            "high": info.get("day_high") or last_high,
            "low": info.get("day_low") or last_low,
            "gap_pct": round(gap * 100, 4) if gap is not None else None,
        }

    try:
        return retry(_one, attempts=3, default={}) or {}
    except Exception:
        return {}


def daily_bar(ticker: str, day: date | None = None) -> dict[str, Any]:
    day = day or now_et().date()
    try:
        h = yf.Ticker(ticker).history(start=(day - timedelta(days=7)).isoformat(), end=(day + timedelta(days=1)).isoformat(), auto_adjust=True)
        if h is None or h.empty:
            return {}
        # match day
        for ts, row in h.iterrows():
            d = ts.tz_convert(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
            if d == day:
                return {"open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]), "close": float(row["Close"]), "day": d.isoformat()}
        row = h.iloc[-1]
        ts = h.index[-1]
        d = ts.tz_convert(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
        return {"open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]), "close": float(row["Close"]), "day": d.isoformat()}
    except Exception:
        return {}


# --- EDGAR ---

def load_cik_map() -> dict[str, str]:
    ensure_dirs()
    path = CACHE / "sec_company_tickers.json"
    mapping: dict[str, str] = {}
    if path.exists() and path.stat().st_mtime > time.time() - 7 * 86400:
        js = json.loads(path.read_text())
    else:
        try:
            r = SESSION.get("https://www.sec.gov/files/company_tickers.json", timeout=30)
            r.raise_for_status()
            path.write_text(r.text)
            time.sleep(0.15)
            js = r.json()
        except Exception:
            if path.exists():
                js = json.loads(path.read_text())
            else:
                return {}
    recs = js.values() if isinstance(js, dict) else js
    for rec in recs:
        t = str(rec.get("ticker") or "").replace(".", "-").upper()
        cik = str(rec.get("cik_str")).zfill(10)
        if t:
            mapping[t] = cik
    return mapping


def fetch_ok(url: str, dest: Path | None = None):
    if dest is not None and dest.exists() and dest.stat().st_size > 0:
        return dest.read_text(errors="replace"), 200
    time.sleep(0.12)
    try:
        r = SESSION.get(url, timeout=40)
    except Exception:
        return None, 0
    if r.status_code != 200:
        return None, r.status_code
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
    return r.text, 200


def pick_exhibit(items: list[dict]) -> str | None:
    names = [it.get("name") or "" for it in items]
    scored = []
    for n in names:
        nl = n.lower()
        if not nl.endswith((".htm", ".html", ".txt")):
            continue
        score = 0
        if re.search(r"ex[-_]?99", nl):
            score += 10
        if "99.1" in nl or "99-1" in nl or "991" in nl:
            score += 8
        if "press" in nl or "release" in nl or "earnings" in nl:
            score += 6
        if score:
            scored.append((score, n))
    if scored:
        scored.sort(reverse=True)
        return scored[0][1]
    for n in names:
        nl = n.lower()
        if nl.endswith((".htm", ".html")) and "index" not in nl:
            return n
    return None


def match_8k(js: dict, report_date: str):
    import pandas as pd

    recent = (js or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    accs = recent.get("accessionNumber", [])
    prim = recent.get("primaryDocument", [])
    target = pd.Timestamp(report_date)
    best = None
    best_abs = 99
    for i, form in enumerate(forms):
        if form not in {"8-K", "8-K/A"}:
            continue
        item = items[i] if i < len(items) else ""
        if "2.02" not in str(item):
            continue
        fd = pd.Timestamp(dates[i])
        delta = abs((fd - target).days)
        if delta <= 3 and delta < best_abs:
            best_abs = delta
            best = {
                "filing_date": dates[i],
                "items": item,
                "accession": accs[i],
                "primary": prim[i] if i < len(prim) else "",
            }
    return best


def fetch_guidance(ticker: str, report_date: str, cik_map: dict[str, str] | None = None) -> dict[str, Any]:
    from stocks.guidance_rules import label_text

    cik_map = cik_map or load_cik_map()
    cik = cik_map.get(ticker.upper())
    empty = {"guidance": "unknown", "tone": "unknown", "headline": "", "excerpt": "", "filing_url": "", "exhibit_url": ""}
    if not cik:
        return empty
    ensure_dirs()
    sub_dest = CACHE / "sec_submissions" / f"{cik}.json"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    text, code = fetch_ok(url, sub_dest)
    if not text:
        return empty
    try:
        js = json.loads(text)
    except Exception:
        return empty
    filing = match_8k(js, report_date)
    if not filing:
        return empty
    cik_int = int(cik)
    acc_n = filing["accession"].replace("-", "")
    idx_dest = CACHE / "sec_index" / f"{cik}_{acc_n}.json"
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_n}/index.json"
    idx_text, _ = fetch_ok(idx_url, idx_dest)
    filename = filing.get("primary") or ""
    if idx_text:
        try:
            idx = json.loads(idx_text)
            picked = pick_exhibit(idx.get("directory", {}).get("item", []))
            if picked:
                filename = picked
        except Exception:
            pass
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_n}/"
    if not filename:
        return {**empty, "filing_url": filing_url, "guidance": "none"}
    doc_dest = CACHE / "sec_exhibits" / f"{cik}_{acc_n}_{filename.replace('/', '_')}"
    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_n}/{filename}"
    html, _ = fetch_ok(doc_url, doc_dest)
    if not html:
        return {**empty, "filing_url": filing_url, "exhibit_url": doc_url, "guidance": "none"}
    text_body = html_to_text(html)
    labeled = label_text(text_body)
    excerpt = text_body[:600]
    return {
        **labeled,
        "excerpt": excerpt,
        "filing_url": filing_url,
        "exhibit_url": doc_url,
        "filing_date": filing.get("filing_date"),
        "accession": filing.get("accession"),
    }


def add_trading_days(start: date, n: int) -> date:
    """Add n US trading days (weekdays only; ignores holidays)."""
    d = start
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left > 0:
        d = d + timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
