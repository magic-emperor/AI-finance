"""
config.py — Pre-registered settings for the honest harness.

EVERYTHING that could be "tuned" lives here and is FROZEN before the
out-of-sample (OOS) run. We do not change these after seeing OOS results —
that is the whole discipline that keeps the test honest.

Decisions are settled by data on unseen trades, not by argument.
"""
from __future__ import annotations

# ── Working timeframe ────────────────────────────────────────────────────────
# 1h bars: yfinance gives ~730d of 1h history in one call for both US equities
# and crypto (BTC-USD/ETH-USD), so we get a long, consistent backtest window
# through a single code path. (Binance is wired for live/paper use later.)
INTERVAL = "1h"
FETCH_BARS = 20000  # effectively "give me all you have for the period"

# ── Watchlists (liquid only — tight spreads or the edge dies) ────────────────
WATCHLISTS = {
    "crypto":    ["BTC-USD", "ETH-USD"],
    "us":        ["AAPL", "NVDA", "AMD", "TSLA", "MSFT", "AMZN", "SPY", "QQQ"],
    "etf":       ["GLD", "TLT", "IWM"],   # diversification for the daily trend track
    # Two-sided, no-up-drift, shortable instruments (Phase 1.7 symmetric long+short):
    "fx":        ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GBPJPY=X"],
    "commodity": ["GC=F", "SI=F", "CL=F"],   # gold, silver, crude oil
}

# ── Transaction costs, per asset class, charged on BOTH entry AND exit ────────
# Deliberately pessimistic. A strategy that survives these is more likely real.
# bps = basis points = 1/100th of a percent. 5 bps = 0.05%.
COSTS = {
    "crypto":    {"fee_bps": 5.0, "slippage_bps": 5.0},   # ~0.20% round-turn
    "us_eq":     {"fee_bps": 0.0, "slippage_bps": 3.0},   # ~0.06% round-turn
    "forex":     {"fee_bps": 0.0, "slippage_bps": 1.0},   # tight major-pair spreads
    "commodity": {"fee_bps": 1.0, "slippage_bps": 3.0},   # gold/oil futures-like
    "nse_eq":    {"fee_bps": 3.0, "slippage_bps": 5.0, "extra_bps": 6.0},  # Phase 3 (STT/stamp/GST)
}


def cost_for(symbol: str) -> dict:
    """Map a symbol to its cost model. Index/US stocks → us_eq default."""
    from engine.data.sources import classify_symbol
    cls = classify_symbol(symbol)
    if cls == "crypto":
        return COSTS["crypto"]
    if cls == "indian_equity":
        return COSTS["nse_eq"]
    if cls == "forex":
        return COSTS["forex"]
    if cls == "commodity":
        return COSTS["commodity"]
    return COSTS["us_eq"]


# ── Money management (capital survival is paramount) ──────────────────────────
RISK = {
    "starting_capital":     500.0,   # the user's reference figure; configurable
    "risk_pct":             0.01,    # risk 1% of capital per trade
    "max_trades_per_day":   2,       # selectivity: few high-conviction trades
    "daily_loss_limit_pct": 0.02,    # stop trading for the day after -2%
}

# ── Breakout strategy parameters (FROZEN) ─────────────────────────────────────
BREAKOUT = {
    "lookback_bars":  120,    # how many recent bars the strategy may look at
    "atr_period":     14,
    "trend_ma":       50,     # trend filter: longs only above this SMA, shorts below
    "vol_lookback":   20,     # break bar volume must beat the median of this many bars
    "stop_atr_mult":  1.0,    # stop = entry ∓ 1*ATR (or the broken level, tighter)
    "target_r":       2.0,    # target = 2R
    "max_hold_bars":  8,      # time stop ≈ one trading day on 1h bars (no overnight drift)
    "min_break_atr":  0.05,   # break must clear the level by at least this * ATR
}

# ── Track A: Opening-Range Breakout (intraday, 15m) — FROZEN ──────────────────
OPENING_RANGE = {
    "interval":       "15m",
    "or_bars":        2,      # opening range = first 2 bars (30 min)
    "lookback_bars":  120,
    "atr_period":     14,
    "trend_ma":       20,     # intraday trend filter (SMA on 15m)
    "stop_atr_mult":  1.0,
    "target_r":       2.0,
    "max_hold_bars":  26,     # ~ one US session of 15m bars (flat by close)
    "min_break_atr":  0.05,
}

# ── Track B: Daily Donchian Trend Breakout (swing, 1d) — FROZEN ───────────────
DONCHIAN = {
    "interval":       "1d",
    "channel":        55,     # breakout of prior 55-day high/low (Turtle-style)
    "sma_trend":      200,    # only trade in the direction of the 200-day trend
    "atr_period":     14,
    "stop_atr_mult":  2.0,    # daily swings need room
    "target_r":       2.5,
    "max_hold_bars":  30,     # days
    "lookback_bars":  300,    # must cover channel + sma_trend
    "long_only":      True,   # start long-only; shorts decided on TRAIN later
}

# ── Mean-reversion fallback parameters (only used if breakout fails the gate) ──
VWAP_REVERSION = {
    "lookback_bars":  120,
    "atr_period":     14,
    "vwap_window":    20,
    "adx_max":        20.0,   # only fade in non-trending (ranging) conditions
    "rsi_period":     14,
    "rsi_low":        25.0,
    "rsi_high":       75.0,
    "stop_atr_mult":  0.25,
    "min_reward_risk": 1.8,
    "max_hold_bars":  8,
}

# ── Phase 1.8 entry-quality filters (Track A) — confirmation to cut weak breakouts ──
ENTRY_FILTERS = {
    "vol_mult": 1.5,    # breakout-bar volume must be >= this * 20-bar average
    "rsi_thr":  55.0,   # momentum: RSI(14) at the breakout must be >= this (long)
    # two_close: require the last TWO closes both beyond the channel (kills 1-bar fakeouts)
}

# ── Phase 1.8 mean-reversion (Track B) — naturally high win rate, fatter tail ──
# Connors RSI-2 style: buy oversold dips WITHIN a long-term uptrend; target = revert
# to the short mean; ATR stop caps the tail (the key risk of mean-reversion).
MEANREV = {
    "interval":      "1d",
    "rsi_period":    2,
    "rsi_entry":     10.0,   # RSI(2) below this = oversold
    "sma_trend":     200,    # only buy dips when price > 200-SMA (uptrend)
    "exit_sma":      5,      # target = the 5-day mean (revert-to-mean)
    "stop_atr_mult": 2.5,    # hard stop = the tail protection
    "max_hold_bars": 10,
    "lookback_bars": 260,
}

# ── Phase 2 paper trading ─────────────────────────────────────────────────────
# Focused, diversified-by-design LONG-ONLY basket (shorts were tested and rejected).
# Different drivers: broad market, tech, crypto, gold, small-caps.
PAPER = {
    "universe": ["SPY", "QQQ", "BTC-USD", "ETH-USD", "GLD", "IWM"],
    "exit_mode": "partial_trail",     # the chosen production exit
    "use_volume_filter": True,        # Phase 1.8: adopted — +12% expectancy, ~30% smaller drawdown
}

# ── Advanced exit modes (Phase 1.6) — tuned on TRAIN, then frozen ─────────────
# Applied by simulator_v2 to the SAME donchian entries; baseline donchian.py is
# untouched. A variant is adopted only if it beats the baseline OOS.
EXIT_V2 = {
    "atr_period":       14,
    "trail_atr_mult":   3.0,    # trail 3*ATR below the highest close (daily trends need room)
    "t1_R":             2.0,    # partial-exit first target (in R)
    "partial_fraction": 0.5,    # take 50% off at T1, trail the rest
    "pyramid_levels":   [1.0, 2.0],   # add at +1R and +2R
    "pyramid_sizes":    [0.5, 0.25],  # DECREASING add sizes (risk-controlled)
    "max_hold_bars":    60,     # let trends run longer than the fixed-target baseline (days)
}

# ── Realistic portfolio constraints (Phase 1.5) ───────────────────────────────
# The user's actual setup: $500, fractional shares (US + crypto). The edge is
# portfolio-level, so we prove it survives these limits before paper trading.
PORTFOLIO = {
    "starting_capital":   500.0,
    "risk_pct":           0.01,
    "max_concurrent":     5,      # also tested at 3 and 8 to see sensitivity
    "fractional":         True,
    "min_position_cash":  5.0,
}
# A sensibly diversified small basket chosen A PRIORI (not cherry-picked winners):
# a broad-market ETF, a tech ETF, crypto, and gold — different drivers.
SMALL_BASKET = ["SPY", "QQQ", "BTC-USD", "GLD"]

# ── Out-of-sample split ───────────────────────────────────────────────────────
# Oldest 60% = TRAIN (where tuning WOULD happen). Most recent 40% = OOS (never
# touched during tuning). We report OOS as the truth; TRAIN only for comparison.
OOS_FRACTION = 0.40

# Rolling walk-forward (robustness check): train window then test window, in bars.
WALK_FORWARD = {"train_bars": 90 * 7, "test_bars": 30 * 7}  # ~90d train / ~30d test (1h eq)

# ── Pre-registered Go/No-Go gate ──────────────────────────────────────────────
# HARD: all must hold on OOS to earn paper trading. Win rate is intentionally NOT
# a hard criterion — profitable trend-following legitimately wins <40% with large
# R:R. The honest profit criteria are: positive expectancy, profit factor, enough
# trades, survivable drawdown.
GATE = {
    "min_trades":         30,
    "min_expectancy_r":   0.15,
    "min_profit_factor":  1.30,
    "max_drawdown_r":    -15.0,
}
# Informational only (reported to inform judgement, never auto-fail):
GATE_INFO = {
    "win_rate_low":   0.40,
    "win_rate_high":  0.60,
    "min_reward_risk": 1.8,
}

# ── Output locations ──────────────────────────────────────────────────────────
import os
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR  = os.path.join(ENGINE_DIR, "data_cache")
OUTPUT_DIR = os.path.join(ENGINE_DIR, "output")
