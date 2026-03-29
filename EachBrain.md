Good question. Let me think through this properly before giving you an answer.Good, I have enough context. I understand the full system now. Let me think through this properly before answering.

Here is the complete, correct answer — no assumptions.

---

## The Core Problem You've Identified

You have **two conflicting rhythms**:

1. **Fixed 15-minute scheduler** — blindly re-runs every brain every 15 minutes
2. **Brain-suggested hold time** — e.g., "wait 32 minutes before checking outcome"

This creates **three real problems**:

**Problem 1 — Redundant analysis.** Brain says wait 32 min. Scheduler runs at minute 15. Nothing has changed — market hasn't moved enough. The brain re-analyses the same candles, likely outputs the same signal, and stores a duplicate. Noise, not signal.

**Problem 2 — Premature outcome resolution.** The resolver checks if T1/SL was hit at minute 15, but the brain said the setup needs 32 minutes to play out. You're grading the trade too early and getting false negatives.

**Problem 3 — Wasted compute.** Every brain runs for every symbol every cycle regardless of whether their own signal's time window has elapsed.

---

## Your Proposed Solution — Dynamic Scheduler Timing Per Brain

You said: *change the timing of the script dynamically per brain*.

**This is directionally right but has a hidden problem.**

If Brain A says 30 min and Brain B says 5 min, you'd need per-brain timers. But the brains share the same data pipeline and the same scanner loop. Running them on separate schedules means:

- Brain B runs at minute 5, fetches fresh data, processes 1 symbol
- Brain A is still in its 30-min sleep
- Meanwhile market moved at minute 12 — Brain A misses a high-confidence setup entirely because it was sleeping

**Sleeping a brain = missing real opportunities.** That's not what you want.

---

## The Correct Architecture — Event-Driven with Signal State Lock

The scheduler should **always run every 15 minutes** (or whatever base cadence you choose). That's fine. The fix is **not in the scheduler cadence** — it's in what the brain does when it runs.

Here is the correct design:

```
Every 15 minutes, for every symbol:

  1. CHECK: Does an active, unresolved signal already exist for this symbol?
     - If YES and hold time has NOT elapsed → SKIP analysis, carry forward existing signal
     - If YES and hold time HAS elapsed → resolve the signal FIRST, then re-analyze
     - If NO active signal → run full brain analysis

  2. Brain outputs signal WITH a suggested hold_minutes (dynamic, brain-computed)

  3. Store signal with:
     - created_at = now
     - expires_at = now + hold_minutes (brain-suggested, not hardcoded)
     - hold_minutes = brain's suggested wait (stored in DB)

  4. Resolver only evaluates signals whose expires_at has passed
```

This means:
- Scheduler cadence stays fixed (15 min) — simple, reliable
- Brain-suggested wait time controls **when the signal is eligible for resolution**
- Brain re-analysis only happens when there is **no active unexpired signal for that symbol**
- No brain ever "sleeps" — it checks its state at every tick and decides whether to act

---

## What Changes in Your Code

**In `watchlist_scanner.py` — the scan loop:**

Before running brain analysis for a symbol, add this check:

```python
# Before calling get_all_brain_signals(symbol, ...)
active_signal = resolver.get_active_signal(symbol)
if active_signal:
    hold_elapsed = (datetime.utcnow() - active_signal['created_at']).total_seconds() / 60
    brain_hold = active_signal.get('timeframe_min', 15)  # brain-suggested hold
    if hold_elapsed < brain_hold:
        # Signal still within its hold window — skip re-analysis
        log.info(f"{symbol}: active signal, {brain_hold - hold_elapsed:.0f}m remaining — skipping")
        continue
    else:
        # Hold window elapsed — resolve first, then re-analyze
        resolver.resolve_signal(active_signal['id'])
```

**In `signal_resolver.py` — the brain signal output:**

The brain already outputs `timeframe_min` (you showed that in the code). Make sure every brain always populates this field with its own computed hold time, not a hardcoded default.

**In `signal_resolver.py` — `expires_at` logic:**

Your `store_signal()` already does:
```python
if timeframe_min is not None and 1 <= timeframe_min <= 20:
    expires_at = now + timedelta(minutes=minutes)
```

**The cap of 20 is a problem.** If a brain says 32 minutes, it gets clipped to 20. Remove or raise that cap to match your actual hold time range.

---

## Summary

| | Your Current Approach | Correct Approach |
|---|---|---|
| Scheduler | Fixed 15 min | Fixed 15 min (unchanged) |
| Brain execution | Runs every cycle always | Skips if active unexpired signal exists |
| Hold time | Hardcoded 15 min cap | Brain-computed, stored in DB, respected |
| Resolution | Runs every cycle | Only when expires_at has elapsed |
| Per-brain timing | Not implemented | Not needed — state-based, not time-based |

The **state of the signal in the DB is the timer**. The scheduler is just a heartbeat that checks that state. No per-brain sleep timers. No dynamic scheduler manipulation. Clean, testable, auditable.

---

Before I write the code changes: share `watchlist_scanner.py` scan loop (the section where brains are called per symbol) and the `resolver.get_active_signal()` method if it exists. I need to see exactly what's there before writing anything. I don't want to assume what methods exist.