# Scoreboard v2 (shadow) — 2026-10-21 18:53Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 1812 total; visible 913 (graded at 5d 246, pending 667, ungradeable 0, no instrument 0); holdout 899 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 169 | typed_announcement | 56 | -0.65% [-2.47%, +0.62%] n=56, 2 wk | 39.3% [27.6%, 52.4%] | 0.7% | - |
| announcement | 5d | 96 | typed_announcement | 36 | +1.36% [-7.47%, +2.16%] n=36, 2 wk | 55.6% [39.6%, 70.5%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 5 | UP | 5 | -0.87% [-1.02%, -0.65%] n=5, 2 wk | 40.0% [11.8%, 76.9%] | 0.5% | - |
| block_deal_buy | 5d | 3 | UP | 3 | +0.78% [+0.78%, +0.78%] n=3, 1 wk | 66.7% [20.8%, 93.9%] | 0.6% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 263 | UP | 263 | -1.85% [-2.17%, -0.69%] n=263, 2 wk | 35.4% [29.8%, 41.3%] | 0.7% | - |
| bulk_deal_buy | 5d | 57 | UP | 57 | -0.17% [-0.17%, -0.17%] n=57, 1 wk | 31.6% [21.0%, 44.5%] | 0.7% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 15 | hint | 13 | +0.42% [+0.33%, +0.43%] n=13, 2 wk | 69.2% [42.4%, 87.3%] | 0.0% | - |
| macro_move | 5d | 5 | hint | 3 | +1.04% [+0.44%, +1.34%] n=3, 2 wk | 100.0% [43.8%, 100.0%] | 0.0% | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 61 | not in B0 | 0 | - | - | - | 3.1% |
| news_multi_source | 5d | 9 | not in B0 | 0 | - | - | - | 4.3% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 4 | UP | 4 | +1.35% [-0.70%, +2.03%] n=4, 2 wk | 50.0% [15.0%, 85.0%] | 0.6% | - |
| sast_acquisition | 5d | 3 | UP | 3 | -0.98% [-0.98%, -0.98%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 193 | hint | 193 | -0.75% [-0.79%, -0.74%] n=193, 2 wk | 50.3% [43.3%, 57.2%] | 0.7% | - |
| volume_breakout | 5d | 46 | hint | 46 | +0.01% [+0.01%, +0.01%] n=46, 1 wk | 56.5% [42.2%, 69.8%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 62 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 27 | not in B0 | 0 | - | - | - | 2.8% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 11 | -1.17% [-1.17%, -1.17%] n=11, 1 wk |
| mid | 52 | -0.68% [-5.18%, -0.50%] n=52, 2 wk |
| small | 64 | -0.17% [-12.05%, +0.02%] n=64, 2 wk |
| unknown | 18 | +5.44% [+5.44%, +5.44%] n=18, 1 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
