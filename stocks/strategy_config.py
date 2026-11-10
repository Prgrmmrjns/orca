"""Load / validate strategy YAML configs (news_event book is config-driven).

The engine reloads `config/strategies/news_event.yaml` on every tick so parameter
edits apply without a code change.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "config" / "strategies"
NEWS_EVENT_PATH = STRATEGIES_DIR / "news_event.yaml"

# Classifier source ids → config source labels
SOURCE_ALIASES: dict[str, str] = {
    "edgar_8k": "8-K",
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
}

# Config category ↔ classifier event_type aliases
CATEGORY_ALIASES: dict[str, set[str]] = {
    "short_report": {"short_report", "short_seller"},
    "short_seller": {"short_report", "short_seller"},
    "ceo_exit": {"ceo_exit", "exec_departure"},
    "exec_departure": {"ceo_exit", "exec_departure"},
    "earnings_miss": {"earnings_miss"},
    "earnings_beat": {"earnings_beat"},
}

ALL_SOURCES = ["8-K", "PRN", "CNBC", "MarketWatch"]

VALID_SIDES = {"long", "short"}
VALID_ENTRY = {"next_open", "next_bar"}  # delay_minutes N handled separately


@dataclass
class HoldSpec:
    days: int | None = None
    bars: int | None = None
    minutes: int | None = None

    def trading_days(self) -> int:
        if self.days is not None:
            return max(1, int(self.days))
        if self.minutes is not None:
            # US cash session ≈ 390 minutes; round up
            return max(1, (int(self.minutes) + 389) // 390)
        if self.bars is not None:
            # treat bars as trading days for the daily paper engine
            return max(1, int(self.bars))
        return 2


@dataclass
class ExitSpec:
    stop_pct: float | None = 0.08
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    exit_at_close: bool = False


@dataclass
class NewsRule:
    name: str
    categories: list[str]
    side: str
    sources: list[str] = field(default_factory=lambda: list(ALL_SOURCES))
    exclude_tags: list[str] = field(default_factory=lambda: ["shareholder_alert"])
    min_gap_pct: float | None = None
    max_gap_pct: float | None = None
    min_eps_surprise_pct: float | None = None
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
class NewsEventConfig:
    book_id: str = "news_event"
    title: str = "News event"
    started: str = "2026-11-10"
    max_positions: int = 6
    max_per_ticker: int = 1
    max_gross_exposure_pct: float = 0.30
    cost_per_fill_pct: float = 0.005
    allow_shorts: bool = True
    cooldown_minutes: int | None = None  # None = never re-enter
    rules: list[NewsRule] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot for the API/dashboard."""
        return {
            "book_id": self.book_id,
            "title": self.title,
            "started": self.started,
            "max_positions": self.max_positions,
            "max_per_ticker": self.max_per_ticker,
            "max_gross_exposure_pct": self.max_gross_exposure_pct,
            "cost_per_fill_pct": self.cost_per_fill_pct,
            "allow_shorts": self.allow_shorts,
            "cooldown_minutes": self.cooldown_minutes,
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
                    "min_eps_surprise_pct": r.min_eps_surprise_pct,
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
            "path": str(NEWS_EVENT_PATH.relative_to(ROOT)),
        }


def default_news_event_config() -> NewsEventConfig:
    """Hardcoded defaults equal to the 2026-11-10 live behaviour."""
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
    return NewsEventConfig(rules=[long_rule, short_rule])


def _as_float(x, default=None):
    if x is None or x == "":
        return default
    return float(x)


def _as_int(x, default=None):
    if x is None or x == "":
        return default
    return int(x)


def _parse_hold(raw: Any) -> HoldSpec:
    if raw is None:
        return HoldSpec(days=2)
    if isinstance(raw, (int, float)):
        return HoldSpec(days=int(raw))
    if not isinstance(raw, dict):
        raise ValueError(f"hold must be a mapping or int, got {type(raw)}")
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
    return ExitSpec(
        stop_pct=_as_float(raw.get("stop_pct")),
        take_profit_pct=_as_float(raw.get("take_profit_pct")),
        trailing_stop_pct=_as_float(raw.get("trailing_stop_pct")),
        exit_at_close=bool(raw.get("exit_at_close") or False),
    )


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
    entry = str(raw.get("entry") or "next_bar").strip()
    if entry not in VALID_ENTRY and not entry.startswith("delay_minutes"):
        raise ValueError(
            f"rule {name}: entry must be next_open|next_bar|delay_minutes:<N>, got {entry!r}"
        )
    size = _as_float(raw.get("size_pct"), 0.05)
    if size is None or size <= 0 or size > 1:
        raise ValueError(f"rule {name}: size_pct must be in (0, 1], got {size}")
    exclude = raw.get("exclude_tags")
    if exclude is None:
        exclude = ["shareholder_alert"]
    if not isinstance(exclude, list):
        raise ValueError(f"rule {name}: exclude_tags must be a list")
    return NewsRule(
        name=name,
        categories=[str(c).lower() for c in cats],
        sources=sources,
        side=side,
        exclude_tags=[str(t).lower() for t in exclude],
        min_gap_pct=_as_float(raw.get("min_gap_pct")),
        max_gap_pct=_as_float(raw.get("max_gap_pct")),
        min_eps_surprise_pct=_as_float(raw.get("min_eps_surprise_pct")),
        min_price=_as_float(raw.get("min_price")),
        min_avg_dollar_volume=_as_float(raw.get("min_avg_dollar_volume")),
        entry=entry,
        size_pct=float(size),
        hold=_parse_hold(raw.get("hold")),
        exits=_parse_exits(raw.get("exits")),
        enabled=bool(raw.get("enabled", True)),
    )


def parse_news_event_dict(data: dict[str, Any]) -> NewsEventConfig:
    if not isinstance(data, dict):
        raise ValueError("news_event config must be a mapping")
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
    if gross is None or gross <= 0:
        raise ValueError("max_gross_exposure_pct must be > 0")
    cost = _as_float(data.get("cost_per_fill_pct"), 0.005)
    if cost is None or cost < 0:
        raise ValueError("cost_per_fill_pct must be >= 0")
    cool = data.get("cooldown_minutes", None)
    if cool == "" or cool == "null":
        cool = None
    if cool is not None:
        cool = int(cool)
        if cool < 0:
            raise ValueError("cooldown_minutes must be >= 0 or null")
    return NewsEventConfig(
        book_id=str(data.get("book_id") or "news_event"),
        title=str(data.get("title") or "News event"),
        started=str(data.get("started") or "2026-11-10"),
        max_positions=int(max_pos),
        max_per_ticker=int(max_per),
        max_gross_exposure_pct=float(gross),
        cost_per_fill_pct=float(cost),
        allow_shorts=bool(data.get("allow_shorts", True)),
        cooldown_minutes=cool,
        rules=rules,
    )


def load_news_event_config(path: Path | None = None) -> NewsEventConfig:
    """Load YAML (or fall back to baked defaults if missing/invalid)."""
    path = path or NEWS_EVENT_PATH
    if not path.exists():
        return default_news_event_config()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        return parse_news_event_dict(raw)
    except Exception as e:
        # Never break the engine on a bad edit — keep last-known-good defaults.
        print(f"news_event config load failed ({path}): {e}; using defaults", flush=True)
        return default_news_event_config()


def normalize_source(src: str | None) -> str | None:
    if not src:
        return None
    key = str(src).strip().lower()
    return SOURCE_ALIASES.get(key, src)


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


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
    et = (candidate.get("news_event_type") or "").lower().strip()
    if not et or not candidate.get("ticker"):
        return None

    # Tags that can exclude (event type itself, rule_tags, explicit news tags)
    tags = {(t or "").lower() for t in (candidate.get("rule_tags") or [])}
    tags.add(et)
    if candidate.get("news_direction"):
        tags.add(str(candidate["news_direction"]).lower())

    src = normalize_source(candidate.get("news_source") or candidate.get("source"))
    gap = _f(candidate.get("gap_pct"))
    surprise = _f(candidate.get("surprise_pct"))
    px = _f(candidate.get("last") or candidate.get("price") or candidate.get("entry_usd"))
    dvol = _f(candidate.get("avg_dollar_volume") or candidate.get("dollar_volume"))

    for rule in cfg.rules:
        if not rule.enabled:
            continue
        if rule.side == "short" and not cfg.allow_shorts:
            continue
        if et not in rule.category_set():
            continue
        if any(t in rule.exclude_tags for t in tags):
            continue
        # Source filter (if candidate has a known source)
        if src and rule.sources:
            allowed = {str(s) for s in rule.sources}
            if src not in allowed:
                continue
        if rule.min_gap_pct is not None and (gap is None or gap < rule.min_gap_pct):
            continue
        if rule.max_gap_pct is not None and (gap is None or gap > rule.max_gap_pct):
            continue
        if rule.min_eps_surprise_pct is not None and (
            surprise is None or surprise < rule.min_eps_surprise_pct
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

        return NewsMatch(
            rule=rule,
            side=rule.side,
            size_pct=rule.size_pct,
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
    """Structural equality used by tests (ignore object identity)."""
    return a.to_public_dict() == b.to_public_dict()
