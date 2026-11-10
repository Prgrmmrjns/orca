"""Orca stocks earnings paper forward-test (US equities). PAPER ONLY."""

__version__ = "0.1.0"
MODE = "paper"
START_DATE = "2026-11-10"
START_CAPITAL_EUR = 1000.0
COST_PCT = 0.005  # 0.5% of notional per fill (entry and exit)
MAX_POSITION_PCT = 0.10
MAX_SHORT_PCT = 0.05
MAX_OPEN = 8
EDGAR_UA = "OrcaPaperEarnings/1.0 (forward-test; contact jonas@prgrmmrjns.dev; github.com/Prgrmmrjns/orca)"
