# Scoreboard v2 (shadow) — 2026-11-03 20:36Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 2741 total; visible 1392 (graded at 5d 891, pending 500, ungradeable 1, no instrument 0); holdout 1349 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 261 | typed_announcement | 88 | -0.98% [-2.66%, +0.62%] n=88, 3 wk | 37.5% [28.1%, 47.9%] | 0.7% | - |
| announcement | 5d | 191 | typed_announcement | 66 | -2.05% [-8.39%, +3.29%] n=66, 3 wk | 37.9% [27.1%, 49.9%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 6 | UP | 6 | -1.70% [-5.85%, -0.65%] n=6, 3 wk | 33.3% [9.7%, 70.0%] | 0.5% | - |
| block_deal_buy | 5d | 5 | UP | 5 | +0.29% [-0.45%, +0.78%] n=5, 2 wk | 60.0% [23.1%, 88.2%] | 0.5% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 378 | UP | 378 | -2.00% [-2.42%, -0.69%] n=378, 3 wk | 33.1% [28.5%, 38.0%] | 0.6% | - |
| bulk_deal_buy | 5d | 299 | UP | 299 | -7.32% [-9.01%, -0.17%] n=299, 2 wk | 10.7% [7.7%, 14.7%] | 0.7% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 19 | hint | 17 | +0.28% [-0.15%, +0.43%] n=17, 3 wk | 58.8% [36.0%, 78.4%] | 0.0% | - |
| macro_move | 5d | 16 | hint | 14 | -0.04% [-0.54%, +0.44%] n=14, 3 wk | 71.4% [45.4%, 88.3%] | 0.0% | - |
| macro_move | 20d | 3 | hint | 1 | +0.87% [+0.87%, +0.87%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.0% | - |
| news_multi_source | 1d | 90 | not in B0 | 0 | - | - | - | 2.7% |
| news_multi_source | 5d | 70 | not in B0 | 0 | - | - | - | 6.2% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 10 | UP | 10 | +0.36% [-0.70%, +2.03%] n=10, 3 wk | 60.0% [31.3%, 83.2%] | 0.7% | - |
| sast_acquisition | 5d | 4 | UP | 4 | -3.73% [-11.96%, -0.98%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 398 | hint | 398 | -1.12% [-1.20%, -0.79%] n=398, 3 wk | 44.0% [39.2%, 48.9%] | 0.7% | - |
| volume_breakout | 5d | 240 | hint | 240 | -3.49% [-4.32%, +0.01%] n=240, 2 wk | 33.8% [28.1%, 39.9%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 68 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 66 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 80 | -7.80% [-8.85%, -1.17%] n=80, 2 wk |
| mid | 204 | -4.45% [-5.81%, +0.41%] n=204, 3 wk |
| small | 308 | -5.50% [-7.11%, +4.73%] n=308, 3 wk |
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
