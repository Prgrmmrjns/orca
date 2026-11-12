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
# Real browser UA required — custom bot UAs are silently timed out by api.nasdaq.com
# (observed from GitHub Actions / datacenter IPs since 2026-11-11).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
NASDAQ_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
YAHOO_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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
    """Daily SPY close converted to EUR index starting at 1000.

    Skips days with non-finite SPY/FX prints so ledgers never store NaN
    (Starlette JSONResponse uses allow_nan=False and would 500 the demo API).
    """
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
            px = _f(row["Close"])
            if px is None or px <= 0:
                continue
            rate = None
            try:
                fx_row = fx.loc[fx.index.normalize() == ts.normalize()]
                rate = _f(fx_row["Close"].iloc[-1]) if len(fx_row) else None
            except Exception:
                rate = None
            if rate is None or rate <= 0:
                # nearest prior
                try:
                    prior = fx[fx.index <= ts]
                    rate = _f(prior["Close"].iloc[-1]) if len(prior) else _f(eurusd())
                except Exception:
                    rate = _f(eurusd())
            if rate is None or rate <= 0:
                continue
            if start_px is None:
                start_px, start_fx = px, rate
            if not start_px or not start_fx:
                continue
            # SPY in EUR terms relative to start equity 1000
            spy_eur = 1000.0 * (px / start_px) * (start_fx / rate)
            if spy_eur != spy_eur:  # NaN guard
                continue
            rows.append(
                {
                    "day": d,
                    "spy_eur": round(spy_eur, 2),
                    "spy_usd": round(px, 4),
                    "eur_usd": round(rate, 5),
                }
            )
        return rows
    except Exception:
        return []


def _cache_load(path: Path) -> dict | list | None:
    if path.exists() and path.stat().st_size > 50:
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _cache_save(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except Exception:
        pass


def _nasdaq_json(day: date) -> dict | None:
    """Fetch Nasdaq earnings calendar JSON; curl + requests + disk cache."""
    import subprocess

    ensure_dirs()
    cache_path = CACHE / "nasdaq" / f"{day.isoformat()}.json"
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}"
    errors: list[str] = []

    # Prefer curl (HTTP/1.1) — HTTP/2 often aborts with INTERNAL_ERROR from this host.
    for attempt in range(3):
        try:
            proc = subprocess.run(
                [
                    "curl", "-sS", "-m", "25", "--http1.1", "--compressed",
                    "-A", NASDAQ_HEADERS["User-Agent"],
                    "-H", f"Origin: {NASDAQ_HEADERS['Origin']}",
                    "-H", f"Referer: {NASDAQ_HEADERS['Referer']}",
                    "-H", f"Accept: {NASDAQ_HEADERS['Accept']}",
                    "-H", f"Accept-Language: {NASDAQ_HEADERS['Accept-Language']}",
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout and len(proc.stdout) > 50:
                js = json.loads(proc.stdout)
                _cache_save(cache_path, js)
                return js
            err = (proc.stderr or "").strip() or f"rc={proc.returncode} empty body"
            errors.append(f"curl[{attempt}]: {err[:160]}")
        except Exception as e:
            errors.append(f"curl[{attempt}]: {type(e).__name__}: {e}")
        time.sleep(0.8 * (attempt + 1))

    def _via_requests():
        r = requests.get(url, headers=NASDAQ_HEADERS, timeout=20)
        if r.status_code == 429:
            raise RuntimeError("rate limited")
        r.raise_for_status()
        return r.json()

    try:
        js = retry(_via_requests, attempts=3, delay=1.2)
        if js:
            _cache_save(cache_path, js)
            return js
    except Exception as e:
        errors.append(f"requests: {type(e).__name__}: {e}")

    cached = _cache_load(cache_path)
    if isinstance(cached, dict):
        print(f"nasdaq {day}: using disk cache after fetch errors: {'; '.join(errors[:4])}", flush=True)
        return cached
    if errors:
        print(f"nasdaq {day}: fetch failed: {'; '.join(errors[:4])}", flush=True)
    return None


def _parse_nasdaq_rows(js: dict, day: date) -> list[dict]:
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


def _yahoo_earnings_page(day: date, offset: int = 0, size: int = 25) -> tuple[int | None, list[dict]]:
    """Parse one Yahoo Finance earnings calendar HTML page."""
    url = (
        f"https://finance.yahoo.com/calendar/earnings?day={day.isoformat()}"
        f"&offset={offset}&size={size}"
    )
    r = requests.get(url, headers=YAHOO_HEADERS, timeout=25)
    r.raise_for_status()
    scripts = re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        r.text,
        re.S,
    )
    for sc in scripts:
        if "SP_EARNINGS" not in sc and "epsestimate" not in sc:
            continue
        try:
            outer = json.loads(sc)
        except Exception:
            continue
        body = outer.get("body", outer)
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                continue
        try:
            res = body["finance"]["result"][0]
            doc = res["documents"][0]
            cols = [c["id"] for c in doc["columns"]]
            rows = [dict(zip(cols, row)) for row in (doc.get("rows") or [])]
            total = res.get("total")
            return (int(total) if total is not None else None), rows
        except Exception:
            continue
    return None, []


def yahoo_earnings(day: date) -> list[dict]:
    """Yahoo Finance earnings calendar (HTML embed) — free Nasdaq fallback."""
    ensure_dirs()
    cache_path = CACHE / "yahoo_earnings" / f"{day.isoformat()}.json"
    cached = _cache_load(cache_path)
    if isinstance(cached, list) and cached:
        out = []
        for rec in cached:
            row = dict(rec)
            row["source"] = "yahoo"
            out.append(row)
        return out

    all_raw: list[dict] = []
    total = None
    page_size = 25
    errors: list[str] = []
    for offset in range(0, 500, page_size):
        try:
            tot, rows = retry(
                lambda off=offset: _yahoo_earnings_page(day, offset=off, size=page_size),
                attempts=2,
                delay=1.0,
            )
        except Exception as e:
            errors.append(f"offset={offset}: {type(e).__name__}: {e}")
            break
        if tot is not None:
            total = tot
        if not rows:
            break
        all_raw.extend(rows)
        if total is not None and len(all_raw) >= total:
            break
        if len(rows) < page_size:
            break
        time.sleep(0.35)

    if not all_raw:
        raise RuntimeError(
            f"yahoo calendar failed for {day}"
            + (f" ({'; '.join(errors[:3])})" if errors else "")
        )

    out: list[dict] = []
    seen: set[str] = set()
    for rec in all_raw:
        ticker = str(rec.get("ticker") or "").replace(".", "-").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        time_code = str(rec.get("startdatetimetype") or "").upper()
        time_label = ""
        if time_code == "BMO":
            time_label = "time-pre-market"
        elif time_code == "AMC":
            time_label = "time-after-hours"
        out.append(
            {
                "cal_date": day.isoformat(),
                "ticker": ticker,
                "name": rec.get("companyshortname") or ticker,
                "time": time_label,
                "eps_reported": parse_num(rec.get("epsactual")),
                "eps_estimate": parse_num(rec.get("epsestimate")),
                "surprise_pct": parse_num(rec.get("epssurprisepct")),
                "fiscal_quarter_ending": None,
                "market_cap": rec.get("intradaymarketcap"),
                "source": "yahoo",
            }
        )
    _cache_save(cache_path, out)
    return out


def edgar_8k_earnings(day: date) -> list[dict]:
    """Fallback: recent SEC EDGAR Atom 8-K Item 2.02 filings (results releases).

    Only covers filings visible on the current feed (not a full calendar), and only
    includes rows we can map to a ticker via the CIK map. Used when Nasdaq + Yahoo fail.
    """
    import feedparser

    ensure_dirs()
    atom = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
    )
    text = None
    try:
        r = requests.get(
            atom,
            headers={"User-Agent": EDGAR_UA, "Accept": "application/atom+xml"},
            timeout=25,
        )
        if r.status_code == 200 and "<feed" in r.text[:800].lower():
            text = r.text
    except Exception:
        text = None
    if not text:
        import subprocess

        try:
            proc = subprocess.run(
                [
                    "curl", "-sS", "-m", "25", "--http1.1", "--compressed",
                    "-A", EDGAR_UA,
                    "-H", "Accept: application/atom+xml",
                    atom,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout and "<feed" in proc.stdout[:800].lower():
                text = proc.stdout
        except Exception:
            text = None
    if not text:
        raise RuntimeError(f"edgar 8-K atom failed for {day}")

    cik_map = load_cik_map()
    cik_to_ticker = {v: k for k, v in cik_map.items()}
    target = day.isoformat()
    out: list[dict] = []
    seen: set[str] = set()
    parsed = feedparser.parse(text)
    for e in parsed.entries[:100]:
        title = (e.get("title") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", e.get("summary") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        if not re.search(r"Item\s*2\.02", summary, re.I) and not re.search(
            r"Item\s*2\.02", title, re.I
        ):
            continue
        # Prefer updated/published date matching target day (UTC date).
        when = e.get("updated") or e.get("published") or ""
        day_hit = target in when or target in summary
        if not day_hit:
            # Atom "current" feed is recent; accept if filed near target (±1 calendar day).
            m = re.search(r"(\d{4}-\d{2}-\d{2})", when)
            if not m:
                continue
            try:
                fd = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if abs((fd - day).days) > 1:
                continue
        cik = None
        m = re.search(r"\((\d{10})\)", title)
        if m:
            cik = m.group(1)
        ticker = cik_to_ticker.get(cik) if cik else None
        if not ticker:
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        company = ""
        m = re.search(r"8-K\s*[-–]\s*(.+?)(?:\s*\(\d{10}\))", title, re.I)
        if m:
            company = m.group(1).strip()
        out.append(
            {
                "cal_date": day.isoformat(),
                "ticker": ticker,
                "name": company or ticker,
                "time": "",
                "eps_reported": None,
                "eps_estimate": None,
                "surprise_pct": None,
                "fiscal_quarter_ending": None,
                "market_cap": None,
                "source": "edgar_8k_2.02",
                "filing_url": (e.get("link") or "").strip(),
            }
        )
    if not out:
        raise RuntimeError(f"edgar 8-K Item 2.02 empty for {day}")
    return out


def nasdaq_earnings(day: date) -> list[dict]:
    """Nasdaq-only calendar rows (raises if Nasdaq + cache miss)."""
    js = _nasdaq_json(day)
    if not js:
        raise RuntimeError(f"nasdaq calendar failed for {day}")
    return _parse_nasdaq_rows(js, day)


def earnings_calendar(day: date) -> list[dict]:
    """Earnings calendar with Nasdaq → Yahoo → EDGAR 8-K Item 2.02 fallbacks.

    Logs which source produced rows. Raises RuntimeError if every source fails.
    """
    errors: list[str] = []

    try:
        rows = nasdaq_earnings(day)
        if rows:
            print(f"earnings calendar {day}: source=nasdaq rows={len(rows)}", flush=True)
            return rows
        errors.append("nasdaq: empty rows")
    except Exception as e:
        errors.append(f"nasdaq: {e}")

    try:
        rows = yahoo_earnings(day)
        if rows:
            print(f"earnings calendar {day}: source=yahoo rows={len(rows)}", flush=True)
            return rows
        errors.append("yahoo: empty rows")
    except Exception as e:
        errors.append(f"yahoo: {e}")

    try:
        rows = edgar_8k_earnings(day)
        if rows:
            print(
                f"earnings calendar {day}: source=edgar_8k_2.02 rows={len(rows)}",
                flush=True,
            )
            return rows
        errors.append("edgar_8k_2.02: empty rows")
    except Exception as e:
        errors.append(f"edgar_8k_2.02: {e}")

    raise RuntimeError(
        f"earnings calendar failed for {day} — all sources exhausted: "
        + "; ".join(errors)
    )


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
