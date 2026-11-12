# Scoreboard v2 (shadow) — 2026-11-12 21:34Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 3518 total; visible 1719 (graded at 5d 1391, pending 325, ungradeable 3, no instrument 0); holdout 1799 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 294 | typed_announcement | 102 | -0.97% [-2.35%, +0.70%] n=102, 4 wk | 38.2% [29.4%, 47.9%] | 0.7% | - |
| announcement | 5d | 281 | typed_announcement | 95 | -2.91% [-8.83%, +2.16%] n=95, 3 wk | 31.6% [23.1%, 41.5%] | 0.7% | - |
| announcement | 20d | 96 | typed_announcement | 36 | -9.58% [-16.82%, -8.92%] n=36, 2 wk | 13.9% [6.1%, 28.7%] | 0.7% | - |
| block_deal_buy | 1d | 14 | UP | 14 | -2.06% [-2.73%, -0.65%] n=14, 3 wk | 14.3% [4.0%, 39.9%] | 0.4% | - |
| block_deal_buy | 5d | 14 | UP | 14 | -1.92% [-3.14%, +0.78%] n=14, 3 wk | 28.6% [11.7%, 54.6%] | 0.4% | - |
| block_deal_buy | 20d | 3 | UP | 3 | -7.45% [-7.45%, -7.45%] n=3, 1 wk | 0.0% [0.0%, 56.2%] | 0.6% | - |
| bulk_deal_buy | 1d | 457 | UP | 457 | -2.28% [-2.71%, -0.88%] n=457, 3 wk | 29.3% [25.3%, 33.7%] | 0.6% | - |
| bulk_deal_buy | 5d | 457 | UP | 457 | -7.13% [-9.59%, +0.12%] n=457, 3 wk | 14.7% [11.7%, 18.2%] | 0.6% | - |
| bulk_deal_buy | 20d | 65 | UP | 65 | -8.98% [-8.98%, -8.98%] n=65, 1 wk | 32.3% [22.2%, 44.4%] | 0.7% | - |
| macro_move | 1d | 25 | hint | 23 | +0.21% [-0.36%, +0.42%] n=23, 4 wk | 56.5% [36.8%, 74.4%] | 0.0% | - |
| macro_move | 5d | 22 | hint | 20 | -0.40% [-1.15%, +0.44%] n=20, 3 wk | 60.0% [38.7%, 78.1%] | 0.0% | - |
| macro_move | 20d | 7 | hint | 5 | +1.37% [+0.87%, +1.50%] n=5, 2 wk | 80.0% [37.6%, 96.4%] | 0.0% | - |
| news_multi_source | 1d | 107 | not in B0 | 0 | - | - | - | 2.5% |
| news_multi_source | 5d | 104 | not in B0 | 0 | - | - | - | 6.0% |
| news_multi_source | 20d | 9 | not in B0 | 0 | - | - | - | 10.0% |
| sast_acquisition | 1d | 10 | UP | 10 | +0.36% [-0.70%, +2.03%] n=10, 3 wk | 60.0% [31.3%, 83.2%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 3 | UP | 3 | -2.35% [-2.35%, -2.35%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| volume_breakout | 1d | 436 | hint | 436 | -1.12% [-1.18%, -0.79%] n=436, 3 wk | 44.7% [40.1%, 49.4%] | 0.7% | - |
| volume_breakout | 5d | 435 | hint | 435 | -3.12% [-4.32%, +0.01%] n=435, 3 wk | 34.9% [30.6%, 39.5%] | 0.7% | - |
| volume_breakout | 20d | 46 | hint | 46 | -3.94% [-3.94%, -3.94%] n=46, 1 wk | 47.8% [34.1%, 61.9%] | 0.7% | - |
| volume_spurt_notice | 1d | 69 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 68 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 27 | not in B0 | 0 | - | - | - | 12.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 158 | -6.95% [-9.14%, -1.17%] n=158, 3 wk |
| mid | 341 | -4.09% [-5.81%, -0.50%] n=341, 3 wk |
| small | 458 | -5.04% [-7.25%, +0.02%] n=458, 3 wk |
| unknown | 54 | -3.43% [-15.98%, +4.44%] n=54, 3 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
