#!/usr/bin/env python3
"""Compare baseline rule strategies vs news-filtered variants (paper only).

BTC/USD daily. No live trades. No TabPFN.
News features use only articles published before each bar (prior-day close).
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.backtest import simulate_portfolio
from orca.config import load_config
from orca.data import fetch_ohlcv, load_ohlcv, save_ohlcv
from orca.logging_setup import setup_logging
from orca.metrics import compute_metrics
from orca.news import (
    attach_news_features,
    collect_news,
    save_daily_features,
)
from orca.news_strategy import generate_news_signals, news_strategy_configs

logger = setup_logging()


def _load_btc_usd(
    timeframe: str,
    limit: int,
    exchange_cfg: dict[str, Any] | None,
    data_dir: Path,
    refresh: bool,
) -> pd.DataFrame:
    pair = "BTC/USD"
    csv_path = data_dir / f"BTC_USD_{timeframe}.csv"
    if not refresh and csv_path.exists():
        return load_ohlcv(csv_path)
    try:
        df = fetch_ohlcv(pair, timeframe=timeframe, limit=limit, exchange_cfg=exchange_cfg)
        save_ohlcv(df, csv_path)
        return df
    except Exception:
        if csv_path.exists():
            return load_ohlcv(csv_path)
        raise


def _lookahead_checks(df: pd.DataFrame, articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify news features on bar T never include same-day-or-later articles."""
    checks: dict[str, Any] = {
        "ok": True,
        "n_bars_checked": 0,
        "violations": [],
        "notes": [],
    }
    if not articles or "news_count" not in df.columns:
        checks["notes"].append("No articles or features to check")
        return checks

    from orca.news import build_daily_news_features

    feats = build_daily_news_features(articles, df.index)
    # Rebuild on the FULL index using only articles with published_at < each
    # sample bar — compare at that bar (avoids single-row ffill artifact).
    sample_ts = list(df.index[:: max(len(df) // 15, 1)])
    # Always include a mid and near-end bar
    sample_ts.append(df.index[len(df) // 2])
    sample_ts.append(df.index[max(len(df) - 5, 0)])
    seen: set[pd.Timestamp] = set()
    for ts in sample_ts:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        if ts in seen:
            continue
        seen.add(ts)
        # Prior calendar day only (matches feature shift: publish day D -> bar D+1)
        arts_before = [
            a
            for a in articles
            if pd.Timestamp(a["published_at"]).tz_convert("UTC").floor("D")
            < ts.floor("D")
        ]
        feats_safe = build_daily_news_features(arts_before, df.index)
        for col in ("news_sentiment", "news_count"):
            a = float(feats.loc[ts, col])
            b = float(feats_safe.loc[ts, col])
            if abs(a - b) > 1e-9:
                checks["ok"] = False
                checks["violations"].append(
                    {
                        "timestamp": str(ts),
                        "column": col,
                        "full": a,
                        "strict_before": b,
                    }
                )
        checks["n_bars_checked"] += 1
        if len(checks["violations"]) >= 5:
            break

    if checks["ok"]:
        checks["notes"].append(
            "Sampled bars: news features match rebuild using only articles with "
            "published_at < bar timestamp on the full calendar index (no look-ahead)."
        )
    else:
        checks["notes"].append("Look-ahead violations detected — see violations list.")
    checks["allowed_article_day_rule"] = (
        "For bar timestamp T (00:00 UTC), features use articles with "
        "published calendar day <= T-1 day (publish day shifted +1 before align)."
    )
    return checks


def _row_from_run(
    label: str,
    strategy_cfg: dict[str, Any],
    summary: dict[str, Any],
    *,
    fee_rate: float,
    candles: int,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    return {
        "label": label,
        "strategy": strategy_cfg.get("name"),
        "news_filter": strategy_cfg.get("news_filter"),
        "strategy_cfg": {
            k: v for k, v in strategy_cfg.items() if k != "label"
        },
        "pair": "BTC/USD",
        "timeframe": "1d",
        "fee_rate": fee_rate,
        "candles": candles,
        "start": start,
        "end": end,
        "return_pct": round(summary["return_pct"], 4),
        "max_drawdown_pct": round(summary["max_drawdown_pct"], 4),
        "n_trades": summary["n_trades"],
        "round_trips": summary.get("round_trips"),
        "win_rate_pct": (
            None
            if summary.get("win_rate_pct") is None
            else round(summary["win_rate_pct"], 2)
        ),
        "sharpe_like": (
            None
            if summary.get("sharpe_like") is None
            else round(summary["sharpe_like"], 4)
        ),
        "buy_hold_return_pct": round(summary["buy_hold_return_pct"], 4),
        "excess_vs_buy_hold_pct": round(summary["excess_vs_buy_hold_pct"], 4),
        "final_equity": round(summary["final_equity"], 2),
        "total_fees": round(summary["total_fees"], 2),
    }


def _rank_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(row["excess_vs_buy_hold_pct"]),
        float(row["return_pct"]),
        float(row["max_drawdown_pct"]),
    )


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orca news-augmented strategy comparison (paper)",
        "",
        f"Generated: {payload['generated_at']} (UTC)",
        f"Mode: `{payload['mode']}` — no live orders. Pair: **BTC/USD** only.",
        f"Timeframe: `{payload['timeframe']}` | fee_rate: `{payload['fee_rate']}` "
        f"({payload['fee_rate'] * 100:.2f}% per side) | initial_cash: `{payload['initial_cash']}`",
        f"News articles cached: **{payload['n_articles']}** "
        f"(sources: {', '.join(payload.get('news_sources', []))})",
        "",
        "## Ranking",
        "",
        "| Rank | Label | Filter | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for i, r in enumerate(payload["ranking"], 1):
        wr = "—" if r["win_rate_pct"] is None else f"{r['win_rate_pct']:.1f}"
        sh = "—" if r["sharpe_like"] is None else f"{r['sharpe_like']:.2f}"
        lines.append(
            f"| {i} | `{r['label']}` | {r['news_filter']} | {r['return_pct']:.2f} | "
            f"{r['max_drawdown_pct']:.2f} | {r['n_trades']} | {wr} | {sh} | "
            f"{r['buy_hold_return_pct']:.2f} | {r['excess_vs_buy_hold_pct']:.2f} |"
        )

    lines.extend(["", "## Baseline vs news (same base rule)", ""])
    by_base: dict[str, list[dict[str, Any]]] = {}
    for r in payload["ranking"]:
        by_base.setdefault(r["strategy"], []).append(r)
    for base, rows in by_base.items():
        base_row = next((x for x in rows if x["news_filter"] == "none"), None)
        lines.append(f"### `{base}`")
        lines.append("")
        if base_row:
            lines.append(
                f"Baseline return **{base_row['return_pct']:.2f}%**, "
                f"excess **{base_row['excess_vs_buy_hold_pct']:.2f}%**, "
                f"trades {base_row['n_trades']}."
            )
        for x in rows:
            if x["news_filter"] == "none":
                continue
            delta = None
            if base_row is not None:
                delta = x["return_pct"] - base_row["return_pct"]
            delta_s = "n/a" if delta is None else f"{delta:+.2f} pp vs baseline"
            lines.append(
                f"- `{x['label']}` ({x['news_filter']}): return {x['return_pct']:.2f}% "
                f"({delta_s}), max DD {x['max_drawdown_pct']:.2f}%, trades {x['n_trades']}"
            )
        lines.append("")

    improved = payload.get("news_improves")
    lines.extend(["## Does news help?", ""])
    if improved is None:
        lines.append("_Insufficient comparison._")
    elif improved:
        lines.append(
            f"**Yes (in-sample):** best news variant `{payload['best_news']['label']}` "
            f"beats its baseline on the ranking key "
            f"(excess {payload['best_news']['excess_vs_buy_hold_pct']:.2f}% vs "
            f"baseline excess {payload['best_news_baseline_excess']:.2f}%)."
        )
    else:
        lines.append(
            "**Not clearly:** no news-filtered variant beat its own baseline on "
            "excess-vs-buy-and-hold in this in-sample window "
            f"(best news `{payload.get('best_news', {}).get('label')}`)."
        )

    la = payload.get("lookahead_check") or {}
    lines.extend(
        [
            "",
            "## Look-ahead check",
            "",
            f"- OK: **{la.get('ok')}** (bars checked: {la.get('n_bars_checked')})",
            f"- Rule: {la.get('allowed_article_day_rule', 'n/a')}",
        ]
    )
    for n in la.get("notes") or []:
        lines.append(f"- {n}")

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- **Scraping fragility:** RSS / Google News HTML-free feeds can change, "
            "rate-limit, or return partial history; cache under `data/news/`.",
            "- **Sentiment quality:** keyword lists + VADER on headlines are crude; "
            "sarcasm, tickers, and publisher suffixes add noise.",
            "- **Coverage gaps:** Google News monthly RSS is capped (~100/query); "
            "not a complete news universe.",
            "- **Look-ahead:** features use articles with `published_at < bar_ts` "
            "(daily bars at 00:00 UTC ⇒ through prior calendar day).",
            "- **In-sample only:** no walk-forward; news thresholds can overfit.",
            "- Kraken public OHLC ~720 daily candles; fees ~0.26% per side.",
            "- Paper simulation only — not predictive of live trading.",
            "",
            "## Re-run",
            "",
            "```bash",
            "cd /workspace/orca && source .venv/bin/activate",
            "python scripts/compare_news_strategies.py",
            "python scripts/compare_news_strategies.py --refresh-news",
            "```",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare news-augmented paper strategies on BTC/USD"
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--fee", type=float, default=None)
    parser.add_argument("--refresh", action="store_true", help="Re-fetch OHLCV")
    parser.add_argument(
        "--refresh-news",
        action="store_true",
        help="Re-fetch RSS / Google News (ignore cache)",
    )
    parser.add_argument(
        "--skip-historical-google",
        action="store_true",
        help="Only use live RSS feeds (shallow history)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    assert cfg["mode"] == "paper"
    setup_logging(
        cfg.get("logging", {}).get("level", "INFO"),
        cfg.get("logging", {}).get("dir", "logs"),
    )

    timeframe = "1d"
    fee_rate = float(
        args.fee
        if args.fee is not None
        else cfg.get("portfolio", {}).get("fee_rate", 0.0026)
    )
    initial_cash = float(cfg.get("portfolio", {}).get("initial_cash", 10_000))
    position_size = float(cfg.get("portfolio", {}).get("position_size", 0.95))
    limit = int(cfg.get("backtest", {}).get("limit", 720))

    data_dir = ROOT / "data"
    news_dir = data_dir / "news"
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    df = _load_btc_usd(timeframe, limit, cfg.get("exchange"), data_dir, args.refresh)
    print(f"BTC/USD: {len(df)} candles ({df.index[0]} → {df.index[-1]})")

    start = pd.Timestamp(df.index[0]).to_pydatetime()
    end = pd.Timestamp(df.index[-1]).to_pydatetime()
    articles = collect_news(
        news_dir,
        start=start,
        end=end,
        refresh=args.refresh_news,
        include_historical_google=not args.skip_historical_google,
    )
    print(f"News articles: {len(articles)}")

    df_news = attach_news_features(df, articles)
    feat_path = news_dir / "daily_features_BTC_USD.csv"
    save_daily_features(
        df_news[
            [
                "news_count",
                "news_sentiment_kw",
                "news_sentiment_vader",
                "news_mention_intensity",
                "news_sentiment",
            ]
        ],
        feat_path,
    )
    print(
        f"News feature coverage: "
        f"{(df_news['news_count'] > 0).mean() * 100:.1f}% bars with count>0 | "
        f"sentiment mean={df_news['news_sentiment'].mean():.4f}"
    )

    lookahead = _lookahead_checks(df_news, articles)
    print(f"Look-ahead check OK={lookahead['ok']} checked={lookahead['n_bars_checked']}")

    rows: list[dict[str, Any]] = []
    for sc in news_strategy_configs():
        label = sc.get("label") or sc["name"]
        signals = generate_news_signals(df_news, sc)
        portfolio, equity = simulate_portfolio(
            signals,
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            position_size=position_size,
        )
        summary = compute_metrics(
            portfolio, equity, signals["close"], periods_per_year=365.0
        )
        row = _row_from_run(
            label,
            sc,
            summary,
            fee_rate=fee_rate,
            candles=len(signals),
            start=str(signals.index[0]) if len(signals) else None,
            end=str(signals.index[-1]) if len(signals) else None,
        )
        rows.append(row)
        print(
            f"  {label:36s} ret={row['return_pct']:+7.2f}%  "
            f"excess={row['excess_vs_buy_hold_pct']:+7.2f}%  "
            f"mdd={row['max_drawdown_pct']:7.2f}%  trades={row['n_trades']}"
        )

    ranking = sorted(rows, key=_rank_key, reverse=True)

    # Did any news variant beat its baseline?
    baselines = {r["strategy"]: r for r in rows if r["news_filter"] == "none"}
    news_rows = [r for r in rows if r["news_filter"] != "none"]
    best_news = None
    news_improves = False
    best_news_baseline_excess = None
    for r in sorted(news_rows, key=_rank_key, reverse=True):
        base = baselines.get(r["strategy"])
        if base is None:
            continue
        if best_news is None:
            best_news = r
            best_news_baseline_excess = base["excess_vs_buy_hold_pct"]
        if r["excess_vs_buy_hold_pct"] > base["excess_vs_buy_hold_pct"] or (
            r["excess_vs_buy_hold_pct"] == base["excess_vs_buy_hold_pct"]
            and r["return_pct"] > base["return_pct"]
        ):
            news_improves = True
            best_news = r
            best_news_baseline_excess = base["excess_vs_buy_hold_pct"]
            break
    if best_news is None and news_rows:
        best_news = sorted(news_rows, key=_rank_key, reverse=True)[0]
        b = baselines.get(best_news["strategy"])
        best_news_baseline_excess = b["excess_vs_buy_hold_pct"] if b else None
        news_improves = False

    sources = sorted({a.get("source", "?") for a in articles})

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "paper",
        "pair": "BTC/USD",
        "timeframe": timeframe,
        "fee_rate": fee_rate,
        "initial_cash": initial_cash,
        "position_size": position_size,
        "n_articles": len(articles),
        "news_sources": sources,
        "news_cache": str(news_dir / "articles.json"),
        "news_features": str(feat_path),
        "lookahead_check": lookahead,
        "n_results": len(rows),
        "ranking": ranking,
        "news_improves": news_improves,
        "best_news": best_news,
        "best_news_baseline_excess": best_news_baseline_excess,
        "best_overall": ranking[0] if ranking else None,
        "caveats": [
            "RSS/Google News scraping fragility",
            "Headline sentiment quality limited",
            "In-sample comparison; overfitting risk",
            "Paper only; no live trades",
        ],
    }

    json_path = logs_dir / "news_augmented_compare.json"
    md_path = logs_dir / "news_augmented_compare.md"
    with json_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print("\n=== Ranking ===")
    for i, r in enumerate(ranking, 1):
        print(
            f"{i:2d}. {r['label']:36s}  ret={r['return_pct']:+7.2f}%  "
            f"excess={r['excess_vs_buy_hold_pct']:+7.2f}%  trades={r['n_trades']}"
        )
    print(f"\nNews improves vs baseline (in-sample)? {news_improves}")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
