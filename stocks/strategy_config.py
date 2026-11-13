"""Load / validate strategy YAML configs for config-driven news books.

The engine reloads `config/strategies/<book_id>.yaml` on every tick so parameter
edits apply without a code change.

Supports:
- Orca schema (fractional size/cost/exposure; flat rule fields)
- Backtest box schema (`book:` / `thresholds:` / percent-point sizes) via normalize
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "config" / "strategies"
NEWS_EVENT_PATH = STRATEGIES_DIR / "news_event.yaml"

# Tuned paper books added 2026-11-10 (box backtest winners). Existing news_event kept.
TUNED_NEWS_BOOK_IDS: list[str] = [
    "guidance_raise_tuned",
    "dilution_shorts_tuned",
    "quality_longs_tuned",
    "guidance_raise_score",
    "gap_filtered_guidance",
    "wide_capacity",
]

# Extended-hours / big-move fade book (paper, start 2026-11-13).
BIGMOVE_BOOK_ID = "bigmove_unified_v1"
EXTENDED_HOURS_BOOK_IDS: list[str] = [BIGMOVE_BOOK_ID]

# Classifier source ids → config source labels
SOURCE_ALIASES: dict[str, str] = {
    "edgar_8k": "8-K",
    "edgar_424b": "8-K",
    "edgar_sc_to": "8-K",
    "8-k": "8-K",
    "8k": "8-K",
    "prnewswire": "PRN",
    "prnewswire_financial": "PRN",
    "prn": "PRN",
    "cnbc_business": "CNBC",
    "cnbc_finance": "CNBC",
    "cnbc": "CNBC",
    "marketwatch": "MarketWatch",
    "marketwatch_realtime": "MarketWatch",
    "news": "PRN",  # generic news candidate source → allow all-source rules via skip
}

# Config category ↔ classifier event_type aliases
CATEGORY_ALIASES: dict[str, set[str]] = {
    "short_report": {"short_report", "short_seller"},
    "short_seller": {"short_report", "short_seller"},
    "ceo_exit": {"ceo_exit", "exec_departure"},
    "exec_departure": {"ceo_exit", "exec_departure"},
    "earnings_miss": {"earnings_miss"},
    "earnings_beat": {"earnings_beat"},
    # bigmove_unified_v1 categories
    "offering": {"offering", "dilution"},
    "dilution": {"offering", "dilution"},
    "other_8k": {"other_8k", "other"},
    "other": {"other_8k", "other"},
    "contract": {"contract", "big_contract"},
    "big_contract": {"contract", "big_contract"},
    "earnings_nosurprise": {"earnings_nosurprise", "earnings"},
    "earnings": {"earnings_nosurprise", "earnings"},
    "mna": {"mna", "mna_deal", "acquisition_target"},
    "mna_deal": {"mna", "mna_deal", "acquisition_target"},
    "acquisition_target": {"mna", "mna_deal", "acquisition_target"},
    "analyst": {"analyst"},
    "no_news_found": {"no_news_found"},
}

ALL_SOURCES = ["8-K", "PRN", "CNBC", "MarketWatch"]

VALID_SIDES = {"long", "short"}
VALID_ENTRY = {"next_open", "next_bar"}
VALID_SIZING = {"fixed", "score_scaled", "vol_scaled"}
VALID_GAP_DIR = {"up", "down", "any", None}
VALID_REGIME = {"any", "spy_above", "spy_below", None}

# Score / vol size multiplier clamp (matches box backtest)
SIZE_MULT_MIN = 0.25
SIZE_MULT_MAX = 2.0


@dataclass
class HoldSpec:
    days: int | None = None
    bars: int | None = None
    minutes: int | None = None

    def trading_days(self) -> int:
        if self.days is not None:
            return max(1, int(self.days))
        if self.minutes is not None:
            return max(1, (int(self.minutes) + 389) // 390)
        if self.bars is not None:
            return max(1, int(self.bars))
        return 2


@dataclass
class ExitSpec:
    stop_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    exit_at_close: bool = False


@dataclass
class NewsRule:
    name: str
    categories: list[str]
    side: str
    sources: list[str] = field(default_factory=lambda: list(ALL_SOURCES))
    exclude_tags: list[str] = field(default_factory=list)
    min_gap_pct: float | None = None
    max_gap_pct: float | None = None
    gap_dir: str | None = None  # up | down | any | None
    regime: str | None = None  # any | spy_above | spy_below | None
    min_eps_surprise_pct: float | None = None
    max_eps_surprise_pct: float | None = None
    min_price: float | None = None
    min_avg_dollar_volume: float | None = None
    entry: str = "next_bar"  # next_open | next_bar | delay_minutes:<N>
    size_pct: float = 0.05
    hold: HoldSpec = field(default_factory=lambda: HoldSpec(days=2))
    exits: ExitSpec = field(default_factory=ExitSpec)
    enabled: bool = True

    def category_set(self) -> set[str]:
        out: set[str] = set()
        for c in self.categories:
            key = (c or "").lower().strip()
            out |= CATEGORY_ALIASES.get(key, {key})
            out.add(key)
        return out


@dataclass
class UniverseSpec:
    min_prev_close: float | None = None
    min_adv20_usd: float | None = None
    exclude_etfs: bool = False


@dataclass
class MegaCapShockSpec:
    min_adv_usd: float = 2.0e9
    min_gap_down_pct: float = 5.0
    trigger: str = ""
    action: str = "alert_only"


@dataclass
class NewsEventConfig:
    book_id: str = "news_event"
    title: str = "News event"
    description: str = ""
    started: str = "2026-11-10"
    max_positions: int = 6
    max_per_ticker: int = 1
    max_gross_exposure_pct: float = 0.30
    cost_per_fill_pct: float = 0.005
    extended_hours_extra_slippage_pct: float = 0.0
    allow_shorts: bool = True
    allow_extended_hours: bool = False
    cooldown_minutes: int | None = None  # None = never re-enter
    sizing: str = "fixed"  # fixed | score_scaled | vol_scaled
    vol_lookback_days: int = 20
    vol_target_pct: float = 2.0
    score_ref: float = 10.0
    chart_filter: str | None = None
    # When true, guidance=raised/cut from the earnings classifier counts as
    # guidance_raise / guidance_cut event types (tuned books). news_event keeps false.
    guidance_bridge: bool = True
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    mega_cap_shock: MegaCapShockSpec | None = None
    rules: list[NewsRule] = field(default_factory=list)
    path: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot for the API/dashboard."""
        return {
            "book_id": self.book_id,
            "title": self.title,
            "description": self.description,
            "started": self.started,
            "max_positions": self.max_positions,
            "max_per_ticker": self.max_per_ticker,
            "max_gross_exposure_pct": self.max_gross_exposure_pct,
            "cost_per_fill_pct": self.cost_per_fill_pct,
            "extended_hours_extra_slippage_pct": self.extended_hours_extra_slippage_pct,
            "allow_shorts": self.allow_shorts,
            "allow_extended_hours": self.allow_extended_hours,
            "cooldown_minutes": self.cooldown_minutes,
            "sizing": self.sizing,
            "vol_lookback_days": self.vol_lookback_days,
            "vol_target_pct": self.vol_target_pct,
            "score_ref": self.score_ref,
            "chart_filter": self.chart_filter,
            "guidance_bridge": self.guidance_bridge,
            "universe": {
                "min_prev_close": self.universe.min_prev_close,
                "min_adv20_usd": self.universe.min_adv20_usd,
                "exclude_etfs": self.universe.exclude_etfs,
            },
            "mega_cap_shock": None
            if self.mega_cap_shock is None
            else {
                "min_adv_usd": self.mega_cap_shock.min_adv_usd,
                "min_gap_down_pct": self.mega_cap_shock.min_gap_down_pct,
                "trigger": self.mega_cap_shock.trigger,
                "action": self.mega_cap_shock.action,
            },
            "rules": [
                {
                    "name": r.name,
                    "enabled": r.enabled,
                    "categories": list(r.categories),
                    "sources": list(r.sources),
                    "side": r.side,
                    "exclude_tags": list(r.exclude_tags),
                    "min_gap_pct": r.min_gap_pct,
                    "max_gap_pct": r.max_gap_pct,
                    "gap_dir": r.gap_dir,
                    "regime": r.regime,
                    "min_eps_surprise_pct": r.min_eps_surprise_pct,
                    "max_eps_surprise_pct": r.max_eps_surprise_pct,
                    "min_price": r.min_price,
                    "min_avg_dollar_volume": r.min_avg_dollar_volume,
                    "entry": r.entry,
                    "size_pct": r.size_pct,
                    "hold": {
                        "days": r.hold.days,
                        "bars": r.hold.bars,
                        "minutes": r.hold.minutes,
                    },
                    "exits": {
                        "stop_pct": r.exits.stop_pct,
                        "take_profit_pct": r.exits.take_profit_pct,
                        "trailing_stop_pct": r.exits.trailing_stop_pct,
                        "exit_at_close": r.exits.exit_at_close,
                    },
                }
                for r in self.rules
            ],
            "path": self.path
            or str((STRATEGIES_DIR / f"{self.book_id}.yaml").relative_to(ROOT)),
        }


def strategy_path(book_id: str) -> Path:
    return STRATEGIES_DIR / f"{book_id}.yaml"


def is_config_driven_book(book_id: str) -> bool:
    if book_id == "news_event":
        return True
    if book_id in TUNED_NEWS_BOOK_IDS:
        return True
    if book_id == BIGMOVE_BOOK_ID:
        return True
    return strategy_path(book_id).exists()


def allows_extended_hours(book_id: str, led: dict | None = None) -> bool:
    """True when this book may fill in pre/after using extended-hours quotes."""
    if led is not None and led.get("allow_extended_hours") is not None:
        return bool(led.get("allow_extended_hours"))
    if book_id in EXTENDED_HOURS_BOOK_IDS:
        return True
    try:
        return bool(load_strategy_config(book_id).allow_extended_hours)
    except Exception:
        return False


def default_news_event_config() -> NewsEventConfig:
    """Hardcoded defaults equal to the 2026-11-10 live news_event behaviour."""
    long_rule = NewsRule(
        name="bullish_news_long",
        categories=["acquisition_target", "big_contract", "fda_approval", "guidance_raise"],
        sources=list(ALL_SOURCES),
        side="long",
        exclude_tags=["shareholder_alert"],
        entry="next_bar",
        size_pct=0.05,
        hold=HoldSpec(days=2),
        exits=ExitSpec(stop_pct=0.08),
        enabled=True,
    )
    short_rule = NewsRule(
        name="bearish_news_short",
        categories=["fraud_probe", "guidance_cut", "short_report", "dilution"],
        sources=list(ALL_SOURCES),
        side="short",
        exclude_tags=["shareholder_alert"],
        entry="next_bar",
        size_pct=0.05,
        hold=HoldSpec(days=2),
        exits=ExitSpec(stop_pct=0.08),
        enabled=True,
    )
    return NewsEventConfig(
        rules=[long_rule, short_rule],
        guidance_bridge=False,
        path="config/strategies/news_event.yaml",
    )


def _as_float(x, default=None):
    if x is None or x == "":
        return default
    return float(x)


def _as_int(x, default=None):
    if x is None or x == "":
        return default
    return int(x)


def _pct_to_frac(x: float | None, *, assume_percent_if_gt: float = 1.0) -> float | None:
    """Backtest YAMLs use percent points (5.0 = 5%); Orca schema uses fractions."""
    if x is None:
        return None
    v = float(x)
    if v > assume_percent_if_gt:
        return v / 100.0
    return v


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _parse_hold(raw: Any) -> HoldSpec:
    if raw is None:
        return HoldSpec(days=2)
    if isinstance(raw, (int, float)):
        return HoldSpec(days=int(raw))
    if not isinstance(raw, dict):
        raise ValueError(f"hold must be a mapping or int, got {type(raw)}")
    # Backtest: {unit: days|bars|minutes, value: N}
    if "unit" in raw and "value" in raw:
        unit = str(raw.get("unit") or "days").lower()
        val = _as_int(raw.get("value"), 2)
        if unit in ("day", "days"):
            return HoldSpec(days=val)
        if unit in ("bar", "bars"):
            return HoldSpec(bars=val)
        if unit in ("minute", "minutes"):
            return HoldSpec(minutes=val)
        raise ValueError(f"hold.unit must be days|bars|minutes, got {unit!r}")
    return HoldSpec(
        days=_as_int(raw.get("days")),
        bars=_as_int(raw.get("bars")),
        minutes=_as_int(raw.get("minutes")),
    )


def _parse_exits(raw: Any) -> ExitSpec:
    if raw is None:
        return ExitSpec()
    if not isinstance(raw, dict):
        raise ValueError(f"exits must be a mapping, got {type(raw)}")
    stop = raw.get("stop_pct")
    tp = raw.get("take_profit_pct")
    trail = raw.get("trailing_stop_pct")
    return ExitSpec(
        stop_pct=_pct_to_frac(_as_float(stop)) if stop is not None else None,
        take_profit_pct=_pct_to_frac(_as_float(tp)) if tp is not None else None,
        trailing_stop_pct=_pct_to_frac(_as_float(trail)) if trail is not None else None,
        exit_at_close=bool(raw.get("exit_at_close") or False),
    )


def _parse_entry(raw: Any) -> str:
    if raw is None:
        return "next_bar"
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        style = str(raw.get("style") or "next_bar").strip()
        delay = raw.get("delay_minutes")
        if delay is not None and delay != "" and float(delay) > 0:
            return f"delay_minutes:{int(delay)}"
        return style
    raise ValueError(f"entry must be a string or mapping, got {type(raw)}")


def normalize_strategy_raw(data: dict[str, Any], default_book_id: str = "news_event") -> dict[str, Any]:
    """Flatten backtest-box YAML into the Orca strategy schema."""
    if not isinstance(data, dict):
        raise ValueError("strategy config must be a mapping")
    out = dict(data)

    # Top-level name/description from box format
    if "book_id" not in out and out.get("name"):
        out["book_id"] = out["name"]
    if "title" not in out:
        out["title"] = str(out.get("description") or out.get("book_id") or default_book_id)[:80]

    book = out.pop("book", None)
    if isinstance(book, dict):
        for k, v in book.items():
            if k == "starting_equity":
                continue
            out.setdefault(k, v)

    # Percent-point → fraction for book-level knobs
    if "cost_per_fill_pct" in out and out["cost_per_fill_pct"] is not None:
        out["cost_per_fill_pct"] = _pct_to_frac(float(out["cost_per_fill_pct"]), assume_percent_if_gt=0.05)
    if "extended_hours_extra_slippage_pct" in out and out["extended_hours_extra_slippage_pct"] is not None:
        out["extended_hours_extra_slippage_pct"] = _pct_to_frac(
            float(out["extended_hours_extra_slippage_pct"]), assume_percent_if_gt=0.05
        )
    if "max_gross_exposure_pct" in out and out["max_gross_exposure_pct"] is not None:
        out["max_gross_exposure_pct"] = _pct_to_frac(
            float(out["max_gross_exposure_pct"]), assume_percent_if_gt=1.5
        )

    rules_in = out.get("rules") or []
    rules_out = []
    for r in rules_in:
        if not isinstance(r, dict):
            raise ValueError("each rule must be a mapping")
        rr = dict(r)
        thr = rr.pop("thresholds", None) or {}
        if isinstance(thr, dict):
            # Map box threshold names → Orca names
            if "min_surprise_pct" in thr and "min_eps_surprise_pct" not in rr:
                rr["min_eps_surprise_pct"] = thr.get("min_surprise_pct")
            if "max_surprise_pct" in thr and "max_eps_surprise_pct" not in rr:
                rr["max_eps_surprise_pct"] = thr.get("max_surprise_pct")
            if "min_adv" in thr and "min_avg_dollar_volume" not in rr:
                rr["min_avg_dollar_volume"] = thr.get("min_adv")
            for k in (
                "min_gap_pct",
                "max_gap_pct",
                "min_price",
                "exclude_tags",
                "min_eps_surprise_pct",
                "max_eps_surprise_pct",
                "min_avg_dollar_volume",
            ):
                if k in thr and k not in rr:
                    rr[k] = thr[k]
        if "entry" in rr:
            rr["entry"] = _parse_entry(rr["entry"])
        if "size_pct" in rr and rr["size_pct"] is not None:
            rr["size_pct"] = _pct_to_frac(float(rr["size_pct"]), assume_percent_if_gt=1.0)
        # Flat hold_days → hold: {days: N}
        if "hold" not in rr and rr.get("hold_days") is not None:
            rr["hold"] = {"days": int(rr.pop("hold_days"))}
        elif "hold_days" in rr and "hold" in rr:
            rr.pop("hold_days", None)
        # Explicit empty exclude_tags stays empty (do not inject shareholder_alert)
        if "exclude_tags" not in rr:
            rr["exclude_tags"] = thr.get("exclude_tags") if isinstance(thr, dict) else None
        rules_out.append(rr)
    out["rules"] = rules_out
    out.setdefault("book_id", default_book_id)
    return out


def _parse_rule(raw: dict[str, Any]) -> NewsRule:
    if not isinstance(raw, dict):
        raise ValueError("each rule must be a mapping")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("rule.name is required")
    cats = raw.get("categories") or []
    if not isinstance(cats, list) or not cats:
        raise ValueError(f"rule {name}: categories must be a non-empty list")
    side = str(raw.get("side") or "").lower().strip()
    if side not in VALID_SIDES:
        raise ValueError(f"rule {name}: side must be long|short, got {side!r}")
    sources = raw.get("sources")
    if sources is None:
        sources = list(ALL_SOURCES)
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"rule {name}: sources must be a non-empty list")
    sources = [str(s) for s in sources]
    entry = _parse_entry(raw.get("entry") or "next_bar")
    if entry not in VALID_ENTRY and not entry.startswith("delay_minutes"):
        raise ValueError(
            f"rule {name}: entry must be next_open|next_bar|delay_minutes:<N>, got {entry!r}"
        )
    size = _as_float(raw.get("size_pct"), 0.05)
    if size is not None and size > 1:
        size = size / 100.0
    if size is None or size <= 0 or size > 1:
        raise ValueError(f"rule {name}: size_pct must be in (0, 1], got {size}")
    exclude = raw.get("exclude_tags")
    if exclude is None:
        # Legacy news_event default; tuned books set [] explicitly
        exclude = ["shareholder_alert"]
    if not isinstance(exclude, list):
        raise ValueError(f"rule {name}: exclude_tags must be a list")
    gap_dir = raw.get("gap_dir")
    if gap_dir is not None and str(gap_dir).strip():
        gap_dir = str(gap_dir).lower().strip()
        if gap_dir not in {"up", "down", "any"}:
            raise ValueError(f"rule {name}: gap_dir must be up|down|any, got {gap_dir!r}")
    else:
        gap_dir = None
    regime = raw.get("regime")
    if regime is not None and str(regime).strip():
        regime = str(regime).lower().strip()
        if regime not in {"any", "spy_above", "spy_below"}:
            raise ValueError(f"rule {name}: regime must be any|spy_above|spy_below, got {regime!r}")
    else:
        regime = None
    hold_raw = raw.get("hold")
    if hold_raw is None and raw.get("hold_days") is not None:
        hold_raw = {"days": int(raw["hold_days"])}
    return NewsRule(
        name=name,
        categories=[str(c).lower() for c in cats],
        sources=sources,
        side=side,
        exclude_tags=[str(t).lower() for t in exclude],
        min_gap_pct=_as_float(raw.get("min_gap_pct")),
        max_gap_pct=_as_float(raw.get("max_gap_pct")),
        gap_dir=gap_dir,
        regime=regime,
        min_eps_surprise_pct=_as_float(raw.get("min_eps_surprise_pct")),
        max_eps_surprise_pct=_as_float(raw.get("max_eps_surprise_pct")),
        min_price=_as_float(raw.get("min_price")),
        min_avg_dollar_volume=_as_float(raw.get("min_avg_dollar_volume")),
        entry=entry,
        size_pct=float(size),
        hold=_parse_hold(hold_raw),
        exits=_parse_exits(raw.get("exits")),
        enabled=bool(raw.get("enabled", True)),
    )


def parse_news_event_dict(data: dict[str, Any], default_book_id: str = "news_event") -> NewsEventConfig:
    data = normalize_strategy_raw(data, default_book_id=default_book_id)
    rules_raw = data.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ValueError("rules must be a non-empty list")
    rules = [_parse_rule(r) for r in rules_raw]
    max_pos = _as_int(data.get("max_positions"), 6)
    if max_pos is None or max_pos < 1:
        raise ValueError("max_positions must be >= 1")
    max_per = _as_int(data.get("max_per_ticker"), 1)
    if max_per is None or max_per < 1:
        raise ValueError("max_per_ticker must be >= 1")
    gross = _as_float(data.get("max_gross_exposure_pct"), 0.30)
    if gross is not None and gross > 1.5:
        gross = gross / 100.0
    if gross is None or gross <= 0:
        raise ValueError("max_gross_exposure_pct must be > 0")
    cost = _as_float(data.get("cost_per_fill_pct"), 0.005)
    if cost is not None and cost > 0.05:
        cost = cost / 100.0
    if cost is None or cost < 0:
        raise ValueError("cost_per_fill_pct must be >= 0")
    ext_slip = _as_float(data.get("extended_hours_extra_slippage_pct"), 0.0) or 0.0
    if ext_slip > 0.05:
        ext_slip = ext_slip / 100.0
    if ext_slip < 0:
        raise ValueError("extended_hours_extra_slippage_pct must be >= 0")
    cool = data.get("cooldown_minutes", None)
    if cool == "" or cool == "null":
        cool = None
    if cool is not None:
        cool = int(cool)
        if cool < 0:
            raise ValueError("cooldown_minutes must be >= 0 or null")
    sizing = str(data.get("sizing") or "fixed").lower().strip()
    if sizing not in VALID_SIZING:
        raise ValueError(f"sizing must be one of {sorted(VALID_SIZING)}, got {sizing!r}")
    book_id = str(data.get("book_id") or default_book_id)
    uni_raw = data.get("universe") if isinstance(data.get("universe"), dict) else {}
    universe = UniverseSpec(
        min_prev_close=_as_float(uni_raw.get("min_prev_close")),
        min_adv20_usd=_as_float(uni_raw.get("min_adv20_usd")),
        exclude_etfs=bool(uni_raw.get("exclude_etfs") or False),
    )
    mega = None
    mega_raw = data.get("mega_cap_shock")
    if isinstance(mega_raw, dict):
        mega = MegaCapShockSpec(
            min_adv_usd=float(_as_float(mega_raw.get("min_adv_usd"), 2.0e9) or 2.0e9),
            min_gap_down_pct=float(_as_float(mega_raw.get("min_gap_down_pct"), 5.0) or 5.0),
            trigger=str(mega_raw.get("trigger") or ""),
            action=str(mega_raw.get("action") or "alert_only"),
        )
    return NewsEventConfig(
        book_id=book_id,
        title=str(data.get("title") or book_id),
        description=str(data.get("description") or ""),
        started=str(data.get("started") or "2026-11-10"),
        max_positions=int(max_pos),
        max_per_ticker=int(max_per),
        max_gross_exposure_pct=float(gross),
        cost_per_fill_pct=float(cost),
        extended_hours_extra_slippage_pct=float(ext_slip),
        allow_shorts=bool(data.get("allow_shorts", True)),
        allow_extended_hours=bool(data.get("allow_extended_hours", False)),
        cooldown_minutes=cool,
        sizing=sizing,
        vol_lookback_days=int(_as_int(data.get("vol_lookback_days"), 20) or 20),
        vol_target_pct=float(_as_float(data.get("vol_target_pct"), 2.0) or 2.0),
        score_ref=float(_as_float(data.get("score_ref"), 10.0) or 10.0),
        chart_filter=data.get("chart_filter"),
        guidance_bridge=bool(
            data["guidance_bridge"]
            if "guidance_bridge" in data
            else (book_id != "news_event")
        ),
        universe=universe,
        mega_cap_shock=mega,
        rules=rules,
        path=data.get("path"),
    )


def load_strategy_config(book_id: str = "news_event", path: Path | None = None) -> NewsEventConfig:
    """Load YAML for a book (falls back to news_event defaults only for that book)."""
    path = path or strategy_path(book_id)
    if not path.exists():
        if book_id == "news_event":
            return default_news_event_config()
        raise FileNotFoundError(f"strategy config missing: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        cfg = parse_news_event_dict(raw, default_book_id=book_id)
        try:
            cfg.path = str(Path(path).resolve().relative_to(ROOT))
        except Exception:
            cfg.path = str(path)
        if not cfg.book_id:
            cfg.book_id = book_id
        return cfg
    except Exception as e:
        if book_id == "news_event":
            print(f"news_event config load failed ({path}): {e}; using defaults", flush=True)
            return default_news_event_config()
        raise


def load_news_event_config(path: Path | None = None) -> NewsEventConfig:
    """Back-compat loader for the original news_event book."""
    try:
        return load_strategy_config("news_event", path=path)
    except Exception as e:
        print(f"news_event config load failed ({path}): {e}; using defaults", flush=True)
        return default_news_event_config()


def normalize_source(src: str | None) -> str | None:
    if not src:
        return None
    key = str(src).strip().lower()
    if key in {"news", "move_trigger", "bigmove_move_trigger"}:
        return None  # unknown / synthetic wire — do not filter out
    return SOURCE_ALIASES.get(key, src)


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def candidate_event_types(
    candidate: dict[str, Any],
    *,
    guidance_bridge: bool = True,
) -> set[str]:
    """Event types implied by news tags (+ optional guidance classifier bridge)."""
    types: set[str] = set()
    et = (candidate.get("news_event_type") or "").lower().strip()
    if et:
        types.add(et)
        types |= CATEGORY_ALIASES.get(et, set())
    for t in candidate.get("rule_tags") or []:
        key = (t or "").lower().strip()
        if key and key not in {"news_event", "news_long", "news_short"}:
            types.add(key)
            types |= CATEGORY_ALIASES.get(key, set())
    if guidance_bridge:
        g = (candidate.get("guidance") or "").lower().strip()
        if g == "raised":
            types.add("guidance_raise")
        elif g == "cut":
            types.add("guidance_cut")
    return types


def _enrich_market_fields(candidate: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Return (gap_pct, surprise_pct, price), fetching gap/price when missing."""
    gap = _f(candidate.get("gap_pct"))
    surprise = _f(candidate.get("surprise_pct"))
    px = _f(candidate.get("last") or candidate.get("price") or candidate.get("entry_usd"))
    ticker = candidate.get("ticker")
    if ticker and (gap is None or px is None):
        try:
            from stocks.market import price_snapshot

            snap = price_snapshot(str(ticker)) or {}
            if gap is None:
                gap = _f(snap.get("gap_pct"))
            if px is None:
                px = _f(snap.get("last") or snap.get("open"))
        except Exception:
            pass
    return gap, surprise, px


def realized_vol_pct(ticker: str, lookback_days: int = 20) -> float | None:
    """Daily return stdev in percent over lookback (approx realized vol)."""
    try:
        import yfinance as yf

        h = yf.Ticker(ticker).history(period=f"{max(lookback_days + 10, 30)}d", interval="1d", auto_adjust=True)
        if h is None or h.empty or "Close" not in h:
            return None
        rets = h["Close"].pct_change().dropna().tail(int(lookback_days))
        if len(rets) < max(5, lookback_days // 2):
            return None
        return float(rets.std() * 100.0)
    except Exception:
        return None


def score_size_multiplier(gap: float | None, surprise: float | None, score_ref: float) -> float:
    """|EPS surprise| or |gap| vs score_ref (percent points), clamped 0.25x–2x."""
    ref = float(score_ref) if score_ref else 10.0
    parts = [abs(x) for x in (gap, surprise) if x is not None]
    if not parts or ref <= 0:
        return 1.0
    score = max(parts)
    return _clamp(score / ref, SIZE_MULT_MIN, SIZE_MULT_MAX)


def vol_size_multiplier(ticker: str, vol_target_pct: float, lookback_days: int) -> float:
    realized = realized_vol_pct(ticker, lookback_days)
    if realized is None or realized <= 0 or vol_target_pct <= 0:
        return 1.0
    return _clamp(float(vol_target_pct) / realized, SIZE_MULT_MIN, SIZE_MULT_MAX)


@dataclass
class NewsMatch:
    rule: NewsRule
    side: str
    size_pct: float
    hold_days: int
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    prefer_open: bool
    delay_minutes: int
    exit_at_close: bool


def match_candidate_to_news_config(
    candidate: dict[str, Any],
    cfg: NewsEventConfig | None = None,
) -> NewsMatch | None:
    """Return the first enabled matching rule for a candidate, or None."""
    cfg = cfg or load_news_event_config()
    ticker = candidate.get("ticker")
    if not ticker:
        return None
    events = candidate_event_types(candidate, guidance_bridge=bool(cfg.guidance_bridge))
    if not events:
        return None

    tags = {(t or "").lower() for t in (candidate.get("rule_tags") or [])}
    tags |= events
    if candidate.get("news_direction"):
        tags.add(str(candidate["news_direction"]).lower())

    src = normalize_source(candidate.get("news_source") or candidate.get("source"))
    gap, surprise, px = _enrich_market_fields(candidate)
    dvol = _f(
        candidate.get("avg_dollar_volume")
        or candidate.get("dollar_volume")
        or candidate.get("adv20_usd")
    )
    spy_above = candidate.get("spy_above_ma50")
    if spy_above is None:
        try:
            from stocks.market import spy_above_ma50

            spy_above = spy_above_ma50()
        except Exception:
            spy_above = None

    # Book-level universe filters
    uni = cfg.universe
    if uni.min_prev_close is not None:
        prev = _f(candidate.get("prev_close")) or px
        if prev is None or prev < uni.min_prev_close:
            return None
    if uni.min_adv20_usd is not None and (dvol is None or dvol < uni.min_adv20_usd):
        # Enrich ADV once if missing
        if dvol is None and ticker:
            try:
                from stocks.market import adv20_usd

                dvol = adv20_usd(str(ticker))
            except Exception:
                dvol = None
        if dvol is None or dvol < uni.min_adv20_usd:
            return None
    if uni.exclude_etfs and (
        candidate.get("is_etf") is True
        or str(candidate.get("quote_type") or "").upper() in {"ETF", "EQUITY ETF"}
    ):
        return None

    for rule in cfg.rules:
        if not rule.enabled:
            continue
        if rule.side == "short" and not cfg.allow_shorts:
            continue
        if not (events & rule.category_set()):
            continue
        if any(t in rule.exclude_tags for t in tags):
            continue
        if src and rule.sources:
            allowed = {str(s) for s in rule.sources}
            if src not in allowed:
                continue
        # Enrich gap if this rule needs it
        needs_gap = (
            rule.min_gap_pct is not None
            or rule.max_gap_pct is not None
            or rule.gap_dir in ("up", "down")
        )
        if needs_gap and gap is None:
            gap, surprise, px = _enrich_market_fields(candidate)
        if rule.gap_dir == "up":
            if gap is None or gap <= 0:
                continue
            if rule.min_gap_pct is not None and gap < rule.min_gap_pct:
                continue
        elif rule.gap_dir == "down":
            if gap is None or gap >= 0:
                continue
            if rule.min_gap_pct is not None and abs(gap) < rule.min_gap_pct:
                continue
        else:
            if rule.min_gap_pct is not None and (gap is None or gap < rule.min_gap_pct):
                continue
        if rule.max_gap_pct is not None and (gap is None or gap > rule.max_gap_pct):
            continue
        if rule.regime == "spy_above" and spy_above is not True:
            continue
        if rule.regime == "spy_below" and spy_above is not False:
            continue
        if rule.min_eps_surprise_pct is not None and (
            surprise is None or surprise < rule.min_eps_surprise_pct
        ):
            continue
        if rule.max_eps_surprise_pct is not None and (
            surprise is None or surprise > rule.max_eps_surprise_pct
        ):
            continue
        if rule.min_price is not None and (px is None or px < rule.min_price):
            continue
        if rule.min_avg_dollar_volume is not None and (
            dvol is None or dvol < rule.min_avg_dollar_volume
        ):
            continue

        entry = rule.entry
        prefer_open = entry == "next_open"
        delay = 0
        if entry.startswith("delay_minutes"):
            prefer_open = False
            try:
                delay = int(entry.split(":", 1)[1] if ":" in entry else entry.split()[-1])
            except Exception:
                delay = 0

        size = float(rule.size_pct)
        if cfg.sizing == "score_scaled":
            size *= score_size_multiplier(gap, surprise, cfg.score_ref)
        elif cfg.sizing == "vol_scaled":
            size *= vol_size_multiplier(str(ticker), cfg.vol_target_pct, cfg.vol_lookback_days)
        size = _clamp(size, 0.001, 1.0)

        return NewsMatch(
            rule=rule,
            side=rule.side,
            size_pct=size,
            hold_days=rule.hold.trading_days(),
            stop_loss=rule.exits.stop_pct,
            take_profit=rule.exits.take_profit_pct,
            trailing_stop=rule.exits.trailing_stop_pct,
            prefer_open=prefer_open,
            delay_minutes=max(0, delay),
            exit_at_close=bool(rule.exits.exit_at_close),
        )
    return None


def configs_equal_for_defaults(a: NewsEventConfig, b: NewsEventConfig) -> bool:
    """Structural equality used by tests (ignore object identity / path)."""
    da, db = a.to_public_dict(), b.to_public_dict()
    da.pop("path", None)
    db.pop("path", None)
    return da == db
