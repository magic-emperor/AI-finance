# CLAUDE.md — Trading Brain AI Agent Instructions

> **This file is the single source of truth for every AI agent or developer
> working on this codebase.** Read it fully before touching any file. No
> exceptions.

---

## 1. WHAT THIS SYSTEM IS

This is a **multi-brain trading AI** for intraday (primary) and long-term
(future) markets — currently focused on Indian equities (NSE/BSE). It consists
of 8 specialist brains that each generate a `BrainSignal`, which is then debated
and voted on by a council (`cortex.py`) before any trade decision is made.

The brains were making losses. We are now fixing them — **one brain at a time**
— to be excellent at analysis and profitable in live trading.

**Do not touch multiple brains at once. Fix one. Prove it. Move to the next.**

---

## 2. THE BRAINS (Current Roster)

| Brain Name         | File                    | Regime                       | Specialization                         | Status |
| ------------------ | ----------------------- | ---------------------------- | -------------------------------------- | ------ |
| AMV-LSTM           | `amv_lstm.py`           | TRENDING_UP / DOWN           | Temporal trend memory, SMA 5/20 + LSTM | Active |
| Regime-Ensemble    | `regime_ensemble.py`    | ALL                          | Market regime detection (meta brain)   | Active |
| Multi-Modal-Fusion | `multi_modal_fusion.py` | RANGING / SQUEEZE            | RSI + MACD + sentiment fusion          | Active |
| Multi-Timeframe    | `multi_timeframe.py`    | TRENDING_UP / DOWN           | Timeframe confluence (15m, 1h, 1d)     | Active |
| Cross-Stock-GNN    | `cross_stock_gnn.py`    | VOLATILE / RANGING           | Sector correlation, institutional flow | Active |
| RL-Weighter        | `rl_weighter.py`        | TRENDING / RANGING           | Kelly-based position sizing, risk mgmt | Active |
| Causal-Ensemble    | `causal_ensemble.py`    | RANGING / SQUEEZE / VOLATILE | Mean reversion, causal lead-lag        | Active |
| Liquidity-Sweep    | `liquidity_sweep.py`    | VOLATILE / TRENDING          | Stop-hunt detection, smart money       | Active |

**Regime-Ensemble is the meta brain** — it overrides the external regime signal.
Every other brain must respect the regime gate before producing a directional
signal.

---

## 3. CORE ARCHITECTURE RULES (Never Break These)

### 3.1 The BrainSignal Contract

Every brain **must** return a `BrainSignal` from `brain_contract.py`. No plain
dicts. No custom objects.

```python
from market_agent.brain.brain_contract import BrainSignal
```

Required fields every brain must populate honestly:

- `direction`: `'BUY'`, `'SELL'`, or `'HOLD'`
- `confidence`: `0.0–1.0` — **must be earned, not assigned**
- `signal_strength`: `0.0–1.0` — magnitude, not direction
- `primary_evidence`: single most important reason, in plain English
- `supporting_factors`: list of strings — what agrees with this signal
- `contra_factors`: list of strings — what argues AGAINST this signal
- `reliability_flags`: dict of booleans — raise flags when something is wrong
- `measurements`: dict of key numeric values (for the Boss Brain to inspect)

### 3.2 Confidence is Earned, Never Assumed

- **Never assign `confidence=0.95` unless a real model with real validation data
  backs it**
- SMA-only signals → cap at `0.68` (as enforced in `amv_lstm.py`)
- RSI-only signals → cap at `0.65`
- Rule-based signals with no model → cap at `0.70`
- Reserve `0.85+` for real ML models with proven backtest accuracy
- The cap exists because **overconfidence kills accounts**

### 3.3 The Regime Gate is Mandatory

Before any brain produces a directional signal, the current regime must allow
it:

```python
BRAIN_REGIME_GATES_UNIFIED = {
    'AMV-LSTM':           ['TRENDING_UP', 'TRENDING_DOWN'],
    'Multi-Modal-Fusion': ['RANGING', 'SQUEEZE'],
    'Multi-Timeframe':    ['TRENDING_UP', 'TRENDING_DOWN'],
    'Cross-Stock-GNN':    ['VOLATILE', 'RANGING'],
    'Causal-Ensemble':    ['RANGING', 'SQUEEZE', 'VOLATILE'],
    'RL-Weighter':        ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING'],
    'Liquidity-Sweep':    ['VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN'],
    'Regime-Ensemble':    ['ALL'],
}
```

If a brain is called in the wrong regime, it must return `direction='HOLD'` with
low confidence. **Not a fallback signal. HOLD.**

### 3.4 ATR is Canonical — Use `brain_utils`

```python
from market_agent.brain.brain_utils import calc_atr
atr = calc_atr(hist)
```

**Never compute ATR inline.** Never use a different formula per brain. The stop
loss, T1, and T2 targets all depend on a consistent ATR.

### 3.5 R:R Ratios Must Be Set Per Brain

Each brain has its own R:R logic because they trade differently:

- Trend-following brains (AMV-LSTM): tight SL = `rr_sl_mult=0.75`, wide T2 =
  `rr_t2_mult=3.5`
- Mean-reversion brains (Causal-Ensemble): wider SL, closer T1
- These must be set as constants at the top of each brain file

---

## 4. UNIFIED REGIME TAXONOMY (Use This Everywhere)

```
TRENDING_UP    — Strong uptrend. Trust trend brains 90%.
TRENDING_DOWN  — Strong downtrend. Trust trend brains 90%.
RANGING        — Sideways. Trust mean-reversion 90%.
VOLATILE       — High volatility. Trust volatility brains 85%.
SQUEEZE        — Bollinger/ATR squeeze. Breakout imminent.
CHAOS          — DO NOT TRADE. All brains output HOLD.
```

Old regime names are DEPRECATED. Migration map in `signal_generators.py`. If you
see `STABLE_TRADING`, `HYBRID_SCAN`, etc. in code — fix it immediately using
`normalize_regime()`.

---

## 5. HOW TO WORK ON A BRAIN (Step-by-Step Protocol)

> Follow this for every brain improvement, no exceptions.

### Step 1: Read Before You Write

1. Read `task.md` — understand which phase and which brain you are on
2. Read `EachBrain.md` — understand the specific brain's purpose and known
   issues
3. Read the brain's own file completely
4. Read `brain_contract.py` — understand what the output must look like
5. Read `signal_generators.py` — understand how this brain is called and gated
6. Read the brain's test file if it exists (e.g. `test_brain_01_regime.py`)

### Step 2: Understand Why It Was Losing

Before writing a single line of code, answer these questions:

- What regime was it trading in when it lost?
- Was the confidence falsely high?
- Was the signal stale?
- Was there no contra-factor being raised?
- Was the HOLD condition too permissive?
- Was there a data quality issue (gaps, bad OHLCV)?

**Document your hypothesis. Don't just fix. Understand.**

### Step 3: Make the Change

- Change only what is needed — surgical, not cosmetic
- Do not refactor while fixing — one concern at a time
- Do not rename things unless they are genuinely wrong
- Check the full file after every change — top to bottom
- Look for: hardcoded values that should be constants, missing error handling,
  silent failures, confidence values that were assumed rather than computed

### Step 4: Write or Update Tests

- Every brain must have a test file: `test_brain_XX_brainname.py`
- Tests must check: correct regime gating, confidence caps, HOLD conditions,
  flag raising
- Tests must use real-looking synthetic data — not random noise
- Tests must all pass before declaring a brain "improved"

### Step 5: Backtest Before Claiming Win Rate

- Do not claim a % win rate without running a proper backtest
- Use at least 3 months of data
- Test across multiple regimes
- Report: win rate, avg profit per winning trade, avg loss per losing trade, max
  drawdown
- If backtest data is unavailable, say so. Do not estimate.

---

## 6. DATA SOURCES — WHAT IS LEGITIMATE

Only use data from these sources. **No assumptions. No fabricated data. No
yfinance for production.**

| Data Type              | Source                                                      | Notes                             |
| ---------------------- | ----------------------------------------------------------- | --------------------------------- |
| OHLCV Intraday (India) | NSE official data / Zerodha Kite API                        | 1m, 5m, 15m, 1h                   |
| OHLCV Daily            | NSE / BSE / Zerodha                                         | EOD data                          |
| Macro/FII/DII          | NSE India website                                           | Institutional flow data           |
| Options chain          | NSE / Sensibull                                             | For PCR, max pain                 |
| News/Sentiment         | Economic Times, Moneycontrol, Reuters                       | Use structured APIs, not scraping |
| Global markets         | Yahoo Finance (acceptable for non-production research only) | SPX, DXY, VIX correlations        |
| Crypto (if used)       | Binance official API                                        | Funding rates, OI                 |

**If data is missing or stale → raise a `reliability_flag`. Do not interpolate
silently.**

---

## 7. WHAT THE BOSS BRAIN (cortex.py) EXPECTS

The Boss Brain reads all `BrainSignal` objects and makes the final decision. It
weights each brain by:

1. `regime_suitability` — is this brain right for the current regime?
2. `method_confidence` — how reliable is this brain's approach in general?
3. `recent_accuracy` — what has this brain's live win rate been?
4. `reliability_flags` — any flags raised discounts the brain's vote

The Boss Brain is NOT magic. It is only as good as the signals it receives. **If
a brain lies (overconfident, stale signal, wrong direction) — the Boss Brain
will make a bad trade.**

---

## 8. THE AEP SYSTEM (AI Enhancement Proposals)

The AEP system is **monitoring only** — it collects observations and suggests
parameter changes. It does NOT auto-apply any code changes.

Files: `aep_trigger.py`, `aep_storage.py`, `aep_council_vote.py`,
`aep_generator.py`, `aep_reviewer.py`

**DISABLED (do not re-enable without explicit instruction):**

- `code_reviewer.py` — AI-generated code drafting
- `pr_system.py` — Auto-merge pipeline

These were disabled intentionally. Auto-merging AI code into a live trading
system without sandboxing is dangerous.

---

## 9. HARDCODING RULES

**Never hardcode these** (use config files, DB, or constants at module top):

- Symbol names inside logic
- Date ranges inside logic
- Win rate percentages as static floats
- Confidence values that should be computed
- Broker API keys or credentials

**Acceptable constants at module top** (not hardcoded inside functions):

- R:R multipliers (they are design decisions, not magic numbers)
- Confidence caps (they are design decisions, documented with rationale)
- Regime gate lists (they are architecture decisions)

Every constant must have a comment explaining WHY it is that value.

---

## 10. INTRADAY FOCUS — CURRENT MISSION

We are focused on **intraday trading (scalping + swing intraday)**:

- Timeframes: 5m, 15m, 1h primarily
- Session: Indian market hours (9:15 AM – 3:30 PM IST)
- No overnight positions (unless explicitly approved)
- Target: 1–3 quality trades per day per symbol
- Preference: quality over quantity — 3 high-confidence trades beats 10 guesses

Long-term / positional trading is a future mission. Do not build for it now.

---

## 11. COMMUNICATION RULES FOR THE AGENT

When working on any task in this codebase, the agent must:

1. **Never assume** — if something is unclear, ask. Provide your hypothesis, but
   mark it as a hypothesis.
2. **Show proof** — if you say a brain had a 60% win rate, show the data. If you
   say a formula is wrong, show why mathematically.
3. **Disagree when right** — if what the user is asking for would make the
   system worse, say so clearly and explain why. Agreeing with everything is how
   trading systems lose money.
4. **One brain at a time** — do not touch multiple brains in a single session
   unless explicitly instructed.
5. **Report the full file state** — after any code change, confirm the entire
   file is consistent: no gaps, no stale imports, no orphaned functions, no
   hardcoded values that should be configurable.
6. **Flag deprecated code** — if you see old regime names, old patterns, or
   deprecated imports, flag them. Do not silently leave them.
7. **Document your changes** — every change to a brain file must include an
   inline comment explaining what changed and why. Future developers (and future
   AI agents) must understand the reasoning.

---

## 12. PHASES OF WORK

> `task.md` contains the current active phase. Always check it first.

| Phase   | Description                                                        |
| ------- | ------------------------------------------------------------------ |
| Phase A | Architecture cleanup — contracts, regime taxonomy, ATR unification |
| Phase B | Brain health monitoring — AEP data collection, accuracy tracking   |
| Phase C | Brain improvement — fix each brain's logic, confidence, gates      |
| Phase D | Backtesting — validate each brain against historical data          |
| Phase E | Live paper trading — observe, measure, compare                     |
| Phase F | Live trading — real capital, tight risk controls                   |

**We are currently in Phase C.** Focus is on improving each brain's core logic
so it does not generate false signals, overconfident positions, or trade in
wrong regimes.

---

## 13. DEFINITION OF "IMPROVED BRAIN"

A brain is considered improved when ALL of the following are true:

- [ ] It correctly returns `HOLD` in regimes it should not trade
- [ ] Its confidence is computed from evidence, not assigned
- [ ] Its `contra_factors` are honest and populated
- [ ] Its `reliability_flags` are raised when data quality is poor
- [ ] It has a test file with passing tests
- [ ] Its backtest shows a win rate above 52% with positive expected value
- [ ] It has been reviewed by a human before merging

Until all boxes are checked, the brain is still being improved.

---

## 14. WHAT COUNTS AS A "WIN"

For intraday:

- **Win**: Trade closes at or beyond T1 before hitting SL
- **Partial win**: Trade closes between entry and T1 (e.g. manual exit)
- **Loss**: Trade hits SL
- **Breakeven**: Trade exits at entry ±0.1%

Expected value formula:

```
EV = (Win Rate × Avg Profit) − (Loss Rate × Avg Loss)
EV must be POSITIVE across at least 50 trades before a brain is trusted
```

---

## 15. NEVER DO THESE THINGS

- ❌ Never use `sleep()` or blocking waits inside brain signal functions
- ❌ Never catch all exceptions silently (`except: pass`) — always log them
- ❌ Never return a BUY/SELL from a brain that has failed data quality checks
- ❌ Never remove `contra_factors` to make a signal look cleaner
- ❌ Never claim a brain is "fixed" without running tests
- ❌ Never enable the auto-code-merge pipeline (`pr_system.py`) without human
  approval
- ❌ Never trade in `CHAOS` regime — every brain must return `HOLD` in CHAOS

---

## 16. FILE MAP (Quick Reference)

| File                     | Purpose                                                       |
| ------------------------ | ------------------------------------------------------------- |
| `brain_contract.py`      | The `BrainSignal` dataclass — THE contract                    |
| `brain_utils.py`         | Shared utilities (ATR, signal dict builder, etc.)             |
| `signal_generators.py`   | Calls all brains, applies regime gates, delta filters         |
| `cortex.py`              | Boss Brain — reads all BrainSignals, makes final decision     |
| `health_monitor.py`      | Tracks per-brain accuracy by regime from real DB data         |
| `boss_prompt_builder.py` | Builds the council prompt for the Boss Brain LLM              |
| `regime_ensemble.py`     | Brain 2 — detects and outputs the current market regime       |
| `aep_storage.py`         | Reads signal_predictions table, writes ai_code_proposals      |
| `council_memory.py`      | Stores council debate history                                 |
| `brain_logger.py`        | Structured logging for all brain activity                     |
| `task.md`                | CURRENT TASK — always read this first                         |
| `EachBrain.md`           | Detailed description of each brain's purpose and known issues |

---

_Last updated: Phase C — Brain-by-brain improvement cycle_ _Primary focus:
Intraday trading, Indian equities_ _Rule: One brain at a time. Prove it works.
Move on._
