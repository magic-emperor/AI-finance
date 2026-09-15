"""
backfill_registry.py — one-time backfill of this session's trials into registry.py.

Run once: python -X utf8 -m engine.backfill_registry
Safe to re-run (register_hypothesis is idempotent on family+name; trials will
duplicate on re-run, so don't re-run casually).
"""
from engine.registry import Registry


def main():
    reg = Registry()

    # 1. Liquidity-sweep mechanism test -- FAILED
    h1 = reg.register_hypothesis(
        family="liquidity/microstructure",
        name="sweep-reversal-mechanism",
        statement="A wick past a prior swing level by >=0.3xATR that closes back "
                  "through predicts forward reversal, vs. an ATR-decile-matched control",
        threshold="p<0.05 AND usable effect size, OOS, across >1 year (pre-committed "
                  "2026-09-13 before running)",
        literature="No supportive literature found; one paper found argues VWAP is a "
                    "trend tool, not a reversal tool -- contradicts the thesis",
    )
    reg.log_trial(
        h1,
        model_version="measure_sweep_mechanism.py (post no-lookahead fix, "
                       "safe_age=max(min_level_age,pivot_bars))",
        data_version="market_agent Postgres, validated store, cross-symbol multi-year",
        result_summary="Mann-Whitney vs ATR-decile-matched control: no significant "
                        "forward-return separation. Full strategy backtest independently "
                        "showed 82% of reported profit came from timeout exits, not the "
                        "thesis; strict (target-before-stop) win rate 27.4% vs 32.2% breakeven.",
        verdict="REJECTED",
    )

    # 2. Donchian trend -- US/crypto/etf -- ACCEPTED pending full gate
    h2 = reg.register_hypothesis(
        family="trend-following",
        name="donchian-us-crypto-etf",
        statement="Donchian-55/SMA-200 trend entry (2R stop, 2.5R target, long-only, "
                  "daily bars) shows positive OOS expectancy on US equities/crypto/ETFs",
        threshold="engine 4-point GO gate: n_trades>=30, expectancy_R>=0.15, "
                  "profit_factor>=1.30, max_dd_R>=-15.0",
        literature="Jegadeesh & Titman 1993 (3-12mo momentum); Moskowitz/Ooi/Pedersen "
                    "2012 Time Series Momentum (58 instruments, ~12mo lookback)",
    )
    reg.log_trial(
        h2,
        model_version="engine/strategy/donchian.py",
        data_version="yfinance cache via CryptoUSFeed, 13 symbols "
                      "(AAPL,AMD,AMZN,BTC-USD,ETH-USD,GLD,IWM,MSFT,NVDA,QQQ,SPY,TLT,TSLA)",
        cost_model_version="config.COSTS v1",
        parameter_config="channel=55, sma_trend=200, atr_period=14, stop_atr_mult=2.0, "
                          "target_r=2.5, max_hold_bars=30",
        result_summary="OOS n=182 trades (train n=282), 2022-07 to 2026-06 (single "
                        "continuous bull run -- not multi-regime). engine 4-point GO gate: "
                        "PASS. NOT yet cleared: 2x-cost stress, latency stress, deflated "
                        "Sharpe vs family trial count, PBO. Expectancy decaying by year "
                        "(see conversation 2026-09-14 re-analysis).",
        verdict="ACCEPTED_PENDING_GATE",
    )

    # 3. Donchian trend -- NSE -- FAILED (sibling trial, same family)
    reg.log_trial(
        h2,
        model_version="engine/strategy/donchian.py (same file, unmodified params)",
        data_version="market_agent Postgres via MarketAgentPostgresFeed, 8 NSE large-caps "
                      "(ITC,HDFCBANK,RELIANCE,TATASTEEL,LT,M&M,ADANIENT,ADANIPORTS).NS",
        cost_model_version="config.COSTS['nse_eq'] -- 3bps fee+5bps slip+6bps STT/stamp/GST",
        parameter_config="channel=55, sma_trend=200, atr_period=14, stop_atr_mult=2.0, "
                          "target_r=2.5, max_hold_bars=30 (unchanged from US/crypto trial "
                          "-- deliberately not re-tuned)",
        result_summary="Clear NO-GO with these US-tuned params on this liquid large-cap "
                        "segment. Root-caused via literature (not 'trend-following is dead "
                        "in India'): signal type mismatch (absolute breakout vs. the "
                        "validated cross-sectional 6-12mo ranking), holding period mismatch "
                        "(max_hold=30d vs. validated 1-3mo hold after 6-12mo formation), "
                        "liquidity segment mismatch (these 8 names are the LIQUID large-cap "
                        "segment where India momentum research shows alpha is weakest -- "
                        "8.51% CAGR liquid vs 19.43% illiquid).",
        verdict="REJECTED",
    )

    # 4. Donchian trend -- FX/commodity -- GO on basic gate (sibling trial, same family)
    reg.log_trial(
        h2,
        model_version="engine/strategy/donchian.py (same file, unmodified params)",
        data_version="yfinance cache via CryptoUSFeed, config.WATCHLISTS['fx']+['commodity'] "
                      "(EURUSD=X,GBPUSD=X,USDJPY=X,GBPJPY=X,GC=F,SI=F,CL=F)",
        cost_model_version="config.COSTS v1 (fx/commodity cost tiers)",
        parameter_config="channel=55, sma_trend=200, atr_period=14, stop_atr_mult=2.0, "
                          "target_r=2.5, max_hold_bars=30 (unchanged -- zero new code, "
                          "reused the already-frozen US/crypto config)",
        result_summary="OOS n=89, expectancy +0.242R, profit_factor 1.441, win_rate 44.9%. "
                        "engine 4-point GO gate: PASS. Per-symbol matches literature: "
                        "commodities (GC=F +0.337R, SI=F +0.749R, CL=F +0.924R, all PF>1.6) "
                        "strongly positive, matches Moskowitz-Ooi-Pedersen; EUR/GBP forex "
                        "crosses negative (EURUSD -0.206R, GBPUSD -0.208R, GBPJPY -0.386R), "
                        "USDJPY positive (+0.274R) -- consistent with modest, pair-dependent "
                        "forex momentum literature. Robust across time: positive in all 3 "
                        "historical thirds, 63% of rolling 6mo windows profitable (24/38). "
                        "NOT yet cleared: 2x-cost stress, latency stress, deflated Sharpe vs "
                        "family trial count, PBO -- do not call this 'validated' without "
                        "that qualifier.",
        verdict="ACCEPTED_PENDING_GATE",
    )

    h, t = reg.dump()
    print("\n=== HYPOTHESES ===")
    print(h.to_string(index=False))
    print(f"\n=== TRIALS ({len(t)}) ===")
    print(t[["id", "hypothesis_id", "verdict", "result_summary"]].to_string(index=False))
    print(f"\ntrend-following family trial count: {reg.family_trial_count('trend-following')}")
    print(f"liquidity/microstructure family trial count: "
          f"{reg.family_trial_count('liquidity/microstructure')}")


if __name__ == "__main__":
    main()
