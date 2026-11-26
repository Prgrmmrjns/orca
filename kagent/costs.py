"""Kraken Pro fee model (fetched from kraken.com/pro/fees, 2026-11-25). Tier 1 = $0 30-day volume.

Spot (tier 1):        maker 0.40%  taker 0.80%   (tier 2 at $2.5K/30d: 0.30% / 0.60%)
Futures/perps (t1):   maker 0.02%  taker 0.05%   (crypto, FX and RWA perps: equities, indices, commodities, xStocks)
xStocks spot (t1):    maker 0.00%  taker 0.08%
Margin (spot):        opening 0.01-0.04% + rollover 0.01-0.04% per 4h (BTC 0.01%, most alts 0.02%) on top of
                      trading fees -> ~22-44%/yr carrying cost. Liquidation fee 2%.
Perp funding:         hourly, longs pay shorts when positive. Kraken 12m realised mean (2025-11..2026-11):
                      BTC +3.7%/yr, ETH +3.6%, most alts slightly negative. Bull markets (2021) ran 20-50%/yr.
"""
FEES = {
    "spot_taker": 0.0080, "spot_maker": 0.0040,
    "perp_taker": 0.0005, "perp_maker": 0.0002,
    "xstock_taker": 0.0008,
    "margin_rollover_per_4h": 0.0002, "margin_open": 0.0002,
}
# Extra slippage / half-spread per side on top of the fee (conservative for a small account)
SLIPPAGE = {"major": 0.0005, "alt": 0.0015, "trad": 0.0005}
MAJORS = {"BTC", "ETH", "SOL", "XRP"}
TRAD = {"GOLD", "SILVER", "WTI", "NDX", "SPX", "EUR"}
# Funding assumptions where no Kraken history: crypto longs pay 10%/yr, shorts receive nothing (conservative);
# RWA perps: 5%/yr cost on either side.
FUND_LONG_CRYPTO = 0.10
FUND_SHORT_CRYPTO = 0.0
FUND_TRAD = 0.05


def per_side_cost(asset: str, venue: str = "perp") -> float:
    fee = FEES["perp_taker"] if venue == "perp" else FEES["spot_taker"]
    slip = SLIPPAGE["trad" if asset in TRAD else "major" if asset in MAJORS else "alt"]
    return fee + slip
