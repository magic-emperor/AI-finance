# Scoreboard v2 (shadow) — 2026-11-13 20:58Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 3659 total; visible 1860 (graded at 5d 1335, pending 522, ungradeable 3, no instrument 0); holdout 1799 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 308 | typed_announcement | 103 | -0.90% [-2.30%, +0.60%] n=103, 4 wk | 38.8% [30.0%, 48.5%] | 0.7% | - |
| announcement | 5d | 279 | typed_announcement | 93 | -2.79% [-8.39%, +2.16%] n=93, 3 wk | 31.2% [22.7%, 41.2%] | 0.7% | - |
| announcement | 20d | 106 | typed_announcement | 39 | -9.89% [-15.23%, -8.92%] n=39, 2 wk | 12.8% [5.6%, 26.7%] | 0.7% | - |
| block_deal_buy | 1d | 15 | UP | 15 | -1.86% [-2.58%, -0.06%] n=15, 4 wk | 20.0% [7.0%, 45.2%] | 0.4% | - |
| block_deal_buy | 5d | 14 | UP | 14 | -1.92% [-3.14%, +0.78%] n=14, 3 wk | 28.6% [11.7%, 54.6%] | 0.4% | - |
| block_deal_buy | 20d | 4 | UP | 4 | -7.48% [-7.60%, -7.45%] n=4, 2 wk | 0.0% [0.0%, 49.0%] | 0.6% | - |
| bulk_deal_buy | 1d | 439 | UP | 439 | -1.99% [-2.35%, -0.93%] n=439, 4 wk | 31.7% [27.5%, 36.2%] | 0.6% | - |
| bulk_deal_buy | 5d | 412 | UP | 412 | -6.94% [-9.01%, -0.17%] n=412, 3 wk | 13.8% [10.8%, 17.5%] | 0.6% | - |
| bulk_deal_buy | 20d | 88 | UP | 88 | -12.50% [-20.09%, -8.38%] n=88, 2 wk | 27.3% [19.1%, 37.4%] | 0.7% | - |
| macro_move | 1d | 26 | hint | 24 | +0.22% [-0.18%, +0.42%] n=24, 4 wk | 58.3% [38.8%, 75.5%] | 0.0% | - |
| macro_move | 5d | 22 | hint | 20 | -0.40% [-1.15%, +0.44%] n=20, 3 wk | 60.0% [38.7%, 78.1%] | 0.0% | - |
| macro_move | 20d | 8 | hint | 6 | +3.03% [+0.87%, +3.47%] n=6, 2 wk | 83.3% [43.6%, 97.0%] | 0.0% | - |
| news_multi_source | 1d | 104 | not in B0 | 0 | - | - | - | 2.6% |
| news_multi_source | 5d | 95 | not in B0 | 0 | - | - | - | 5.8% |
| news_multi_source | 20d | 18 | not in B0 | 0 | - | - | - | 9.8% |
| sast_acquisition | 1d | 10 | UP | 10 | +0.36% [-0.70%, +2.03%] n=10, 3 wk | 60.0% [31.3%, 83.2%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 3 | UP | 3 | -2.35% [-2.35%, -2.35%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| volume_breakout | 1d | 486 | hint | 486 | -1.10% [-1.16%, -0.86%] n=486, 4 wk | 44.2% [39.9%, 48.7%] | 0.7% | - |
| volume_breakout | 5d | 435 | hint | 435 | -3.12% [-4.32%, +0.01%] n=435, 3 wk | 34.9% [30.6%, 39.5%] | 0.7% | - |
| volume_breakout | 20d | 83 | hint | 83 | -8.34% [-13.80%, -3.94%] n=83, 2 wk | 34.9% [25.6%, 45.7%] | 0.7% | - |
| volume_spurt_notice | 1d | 76 | not in B0 | 0 | - | - | - | 2.0% |
| volume_spurt_notice | 5d | 68 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 38 | not in B0 | 0 | - | - | - | 14.2% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 146 | -6.63% [-8.85%, -1.17%] n=146, 3 wk |
| mid | 341 | -4.09% [-5.81%, -0.50%] n=341, 3 wk |
| small | 455 | -4.95% [-7.11%, +0.02%] n=455, 3 wk |
| unknown | 22 | +2.22% [-12.26%, +5.44%] n=22, 2 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
