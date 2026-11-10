"""Orca stocks earnings paper forward-test (US equities). PAPER ONLY.

Five parallel €1,000 books since 2026-11-10: agent + four rule books.
"""

__version__ = "0.2.0"
MODE = "paper"
START_DATE = "2026-11-10"
START_CAPITAL_EUR = 1000.0
COST_PCT = 0.005  # 0.5% of notional per fill (entry and exit)
MAX_POSITION_PCT = 0.10
MAX_SHORT_PCT = 0.05
MAX_OPEN = 8
EDGAR_UA = "OrcaPaperEarnings/1.0 (forward-test; contact jonas@prgrmmrjns.dev; github.com/Prgrmmrjns/orca)"
