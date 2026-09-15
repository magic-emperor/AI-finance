# CLAUDE.md — Aegis Trading Brain: Agent Instructions

> **Single source of truth for every AI agent or developer working on this codebase.**
> Read this fully before touching any file. No exceptions.

---

## 1. WHAT THIS SYSTEM IS

Aegis is a **multi-brain AI trading system** focused on intraday Indian equities (NSE/BSE).
Eight specialist brains each produce a `BrainSignal`. A coordinator (`signal_generators.py`)
applies regime gates and collects the signals. A Boss Brain (`cortex.py`) votes on them and
issues a `CouncilVerdict`. Paper trading executes verdicts and the signal resolver tracks outcomes.

**Current mission:** Connect the completed, tested brain pipeline to production so real trade
outcomes accumulate in the DB — then use that data to backtest and harden each brain.

---

## 2. BRAIN ROSTER (All files in `market_agent/brain/`)

| # | Brain Name         | File                    | Regime Gate                          | Status         |
|---|--------------------|-------------------------|--------------------------------------|----------------|
| 1 | AMV-LSTM           | `amv_lstm.py`           | TRENDING_DOWN                        | Built + Tested |
| 1b| AMV-LSTM-Uptrend   | `amv_lstm_uptrend.py`   | TRENDING_UP                          | Built + Tested |
| 2 | Regime-Ensemble    | `regime_ensemble.py`    | ALL (meta — runs first, always)      | Built + Tested |
| 3 | Multi-Modal-Fusion | `multi_modal_fusion.py` | RANGING, SQUEEZE                     | Built + Tested |
| 4 | Multi-Timeframe    | `multi_timeframe.py`    | TRENDING_UP, TRENDING_DOWN           | Built + Tested |
| 5 | Cross-Stock-GNN    | `cross_stock_gnn.py`    | VOLATILE, RANGING                    | Built + Tested |
| 6 | Causal-Ensemble    | `causal_ensemble.py`    | RANGING, SQUEEZE, VOLATILE           | Built + Tested |
| 7 | Liquidity-Sweep    | `liquidity_sweep.py`    | VOLATILE, TRENDING_UP, TRENDING_DOWN | Built + Tested |
| 8 | RL-Weighter        | `rl_weighter.py`        | ALL (sizer, not voter)               | Built + Tested |

**Regime-Ensemble runs first and sets the regime for all other brains.**
**RL-Weighter is a position sizer — it goes into `brain_results[]`, NOT `signals[]`.**

---

## 3. CORE RULES (Never Break These)

### 3.1 BrainSignal Contract
Every brain returns a `BrainSignal` from `brain_contract.py`. No plain dicts. No exceptions.

```python
from market_agent.brain.brain_contract import BrainSignal
```

Required fields: `direction` (BUY/SELL/HOLD), `confidence` (0.0–1.0), `signal_strength`,
`primary_evidence`, `supporting_factors`, `contra_factors`, `reliability_flags`, `measurements`.

### 3.2 Confidence Caps (Earned, Never Assumed)
| Signal source | Cap |
|---|---|
| SMA-only crossover | 0.68 |
| RSI-only | 0.65 |
| Rule-based, no model | 0.70 |
| Multi-factor, no ML | 0.82 |
| ML model with proven backtest | 0.95 |

**Falsely high confidence causes real financial loss. If no model backs it, cap it.**

### 3.3 Regime Gate is Mandatory
Every brain checks regime before firing a directional signal. Wrong regime → `direction='HOLD'`.
The gate list is in `signal_generators.py` as `BRAIN_REGIME_GATES_UNIFIED`. Do not duplicate it.

### 3.4 Valid Regimes Only
```
TRENDING_UP    TRENDING_DOWN    RANGING    VOLATILE    SQUEEZE    CHAOS
```
`HYBRID_SCAN`, `PATH_A`, `VOLATILE_CHAOS`, `STABLE_TRADING` — **all deprecated and invalid**.
If you see them in code or data: fix immediately using `normalize_regime()` in `signal_generators.py`.

### 3.5 ATR is Canonical
```python
from market_agent.brain.brain_utils import calc_atr
atr = calc_atr(hist)
```
Never compute ATR inline. Never use a different formula. Stop losses and targets depend on this.

### 3.6 CHAOS = No Trades
Every brain returns `direction='HOLD'` when regime is CHAOS. No exceptions.

---

## 4. PIPELINE ARCHITECTURE

```
Data (AngelOne / yfinance fallback)
  → validate_ohlcv()
  → Regime-Ensemble brain  →  regime string
  → signal_generators.py   →  List[BrainSignal]   (regime-gated, per brain)
  → cortex.py              →  CouncilVerdict       (weighted vote)
  → circuit_breaker check
  → paper_trader           →  open/close positions
  → signal_resolver        →  resolve outcomes → brain_predictions table
  → health_monitor         →  per-brain accuracy tracking
```

**Key files:**
| File | Purpose |
|---|---|
| `brain/signal_generators.py` | Coordinator: calls all brains, applies gates, builds signal dicts |
| `brain/cortex.py` | Boss Brain: weighted voting, CouncilVerdict |
| `brain/health_monitor.py` | Per-brain accuracy tracking from DB |
| `brain/brain_contract.py` | BrainSignal dataclass — the contract |
| `brain/brain_utils.py` | Canonical ATR, RSI, MACD, swing levels |
| `runner/watchlist_scanner.py` | Main loop: data → pipeline → resolve → learn |
| `runner/autonomous_scout.py` | 10-minute scan scheduler |
| `learning/signal_resolver.py` | Stores predictions, resolves outcomes |
| `data/storage/postgres.py` | Single ORM source of truth |
| `task.md` | **Current active tasks — always check first** |

---

## 5. DATABASE STATE (as of 2026-03-31)

| Table | Rows | Notes |
|---|---|---|
| `signal_predictions` | 473 | All from old pipeline (invalid regimes). Noisy but resolvable. |
| `brain_predictions` | 0 | Schema ready. New pipeline has never written here yet. |
| `council_verdicts` | 0 | Schema ready. |
| `paper_trade_signals` | 0 | Schema ready. |

**The new 8-brain pipeline is built and tested but not yet connected to production.**
The primary blocker is that `USE_PATH_A_SEVEN_BRAINS` defaults to `false` and Path A
passes `regime="PATH_A"` (invalid) when enabled.

---

## 6. CURRENT PHASE: PIPELINE CONNECTION

**Phases completed:**
- Phase 0: Pre-test fixes (regime names, BrainSignal contract violations)
- Phase 2: All 8 brain unit tests passing (9 test files, 40+ test cases)
- Phase A: Logic improvements A1–A12 all complete
- Phase B: B1–B9 all complete

**Current goal:** Wire the new brain pipeline to production so `brain_predictions`,
`council_verdicts`, and `paper_trade_signals` start accumulating real data.

**Next gate:** Phase C (backtest + parameter tuning) cannot start until:
- [ ] `brain_predictions` has at least 30 signals per brain per regime
- [ ] Signal resolver is resolving outcomes against real price data
- [ ] `paper_trade_signals` is tracking open/closed positions

---

## 7. HOW TO WORK ON A TASK

1. Read `task.md` first — understand exactly what phase/task you are on
2. Read the relevant brain file(s) and any referenced test file
3. Understand why the current state is what it is before changing it
4. Make surgical changes — one concern at a time
5. Run the relevant test file after any change: `pytest tests/test_brain_XX.py -v`
6. If a test breaks, fix it before moving on — never leave tests failing
7. Update `task.md` when a task is complete

---

## 8. WHAT NEVER TO DO

- ❌ Pass `regime="PATH_A"` or `regime="HYBRID_SCAN"` or any deprecated regime string
- ❌ Assign `confidence=0.95` without a real ML model backing it
- ❌ Return BUY/SELL from a brain that failed data quality checks
- ❌ Remove `contra_factors` to make a signal look cleaner
- ❌ Use `sleep()` or blocking waits inside brain signal functions
- ❌ Catch all exceptions silently (`except: pass`) — always log them
- ❌ Enable `pr_system.py` or `code_reviewer.py` — both disabled intentionally
- ❌ Trade in CHAOS regime
- ❌ Touch multiple brains in one session unless explicitly instructed
- ❌ Claim a brain is "fixed" without running its test file

---

## 9. TESTING

Every brain has a test file in `tests/`. Run tests with:
```bash
cd "d:/AI Agent Finance"
pytest tests/ -v                          # all tests
pytest tests/test_brain_02_amv_lstm.py -v # specific brain
```

All 9 test files must pass before any production change.

---

## 10. DATA SOURCES (Legitimate Only)

**Corrected 2026-09-13 — this table previously described the intended design,**
**not the actual wiring. Verified against the live code, not assumed.**

| Data type | Source | Notes |
|---|---|---|
| OHLCV Historical (India, all timeframes) | Breeze API (ICICI Direct) → yfinance fallback | `market_agent/scripts/rebuild_db.py`. Breeze caps at ~1000 candles/call (no pagination) — falls back to yfinance automatically when the requested period exceeds that. |
| OHLCV Live quotes (India) | AngelOne SmartAPI | `angel_one_client.py` — live/intraday quotes only, **not** used for historical backfill despite what this table previously claimed. |
| OHLCV Historical (US equities, commodities, forex) | Yahoo Finance (yfinance) | Primary, not just "research only" — this is the actual production source for these asset classes. |
| OHLCV Historical (Crypto) | Binance public data mirror (`data-api.binance.vision`) → yfinance fallback | OHLCV only. Funding rate / open interest ingestion is **not built** — do not assume it exists when working on cascade/liquidation-risk features. |
| Macro / FII / DII | `nsepython` via `signal_generators.get_fii_dii_signal()` | Real, live source; honestly falls back to HOLD/0.0 confidence on failure. `institutional_flow.py` (a dead file that faked this data with `np.random`) has been deleted — it was never wired into anything live. |

If data is missing or stale → raise a `reliability_flag`. Do not interpolate silently.

---

## 11. AEP SYSTEM (Monitoring Only)

AEP collects observations and suggests parameter changes. It does NOT auto-apply code changes.

Files: `aep_trigger.py`, `aep_storage.py`, `aep_council_vote.py`, `aep_generator.py`, `aep_reviewer.py`

**Disabled (do not re-enable without explicit instruction):**
- `code_reviewer.py` — AI code drafting
- `pr_system.py` — auto-merge pipeline

---

_Last updated: 2026-03-31 — Pipeline connection phase_
_Primary focus: Indian equities intraday, 5m/15m/1h timeframes_
_Rule: Understand before changing. Test before claiming done._
