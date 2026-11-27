# Scoreboard v2 (shadow) — 2026-11-27 22:54Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 4861 total; visible 2435 (graded at 5d 1795, pending 636, ungradeable 4, no instrument 0); holdout 2426 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 399 | typed_announcement | 130 | -0.98% [-2.07%, +0.04%] n=130, 5 wk | 38.5% [30.5%, 47.0%] | 0.7% | - |
| announcement | 5d | 346 | typed_announcement | 114 | -2.87% [-6.88%, +0.83%] n=114, 4 wk | 32.5% [24.6%, 41.5%] | 0.7% | - |
| announcement | 20d | 209 | typed_announcement | 74 | -11.16% [-15.60%, -7.89%] n=74, 3 wk | 12.2% [6.5%, 21.5%] | 0.7% | - |
| block_deal_buy | 1d | 44 | UP | 44 | -0.85% [-2.42%, -0.25%] n=44, 5 wk | 59.1% [44.4%, 72.3%] | 0.4% | - |
| block_deal_buy | 5d | 41 | UP | 41 | -0.99% [-2.75%, +0.29%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 20d | 5 | UP | 5 | -4.93% [-7.45%, -1.17%] n=5, 2 wk | 20.0% [3.6%, 62.4%] | 0.5% | - |
| bulk_deal_buy | 1d | 591 | UP | 591 | -1.89% [-2.25%, -0.92%] n=591, 5 wk | 34.5% [30.8%, 38.4%] | 0.7% | - |
| bulk_deal_buy | 5d | 559 | UP | 559 | -6.75% [-8.54%, -2.97%] n=559, 4 wk | 14.0% [11.3%, 17.1%] | 0.7% | - |
| bulk_deal_buy | 20d | 322 | UP | 322 | -16.32% [-19.85%, -8.38%] n=322, 3 wk | 10.6% [7.7%, 14.4%] | 0.6% | - |
| macro_move | 1d | 38 | hint | 36 | +0.06% [-0.34%, +0.36%] n=36, 5 wk | 61.1% [44.9%, 75.2%] | 0.0% | - |
| macro_move | 5d | 32 | hint | 30 | -0.12% [-0.95%, +0.45%] n=30, 4 wk | 63.3% [45.5%, 78.1%] | 0.0% | - |
| macro_move | 20d | 18 | hint | 16 | +2.56% [+0.37%, +3.25%] n=16, 3 wk | 62.5% [38.6%, 81.5%] | 0.0% | - |
| news_multi_source | 1d | 130 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 122 | not in B0 | 0 | - | - | - | 5.4% |
| news_multi_source | 20d | 79 | not in B0 | 0 | - | - | - | 9.7% |
| sast_acquisition | 1d | 17 | UP | 17 | -0.16% [-0.89%, +0.99%] n=17, 5 wk | 52.9% [31.0%, 73.8%] | 0.7% | - |
| sast_acquisition | 5d | 15 | UP | 15 | -4.97% [-9.40%, -1.09%] n=15, 4 wk | 26.7% [10.9%, 52.0%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 634 | hint | 634 | -1.22% [-1.46%, -1.08%] n=634, 5 wk | 41.5% [37.7%, 45.4%] | 0.7% | - |
| volume_breakout | 5d | 593 | hint | 593 | -3.32% [-4.12%, -2.07%] n=593, 4 wk | 32.2% [28.6%, 36.1%] | 0.7% | - |
| volume_breakout | 20d | 274 | hint | 274 | -10.18% [-12.15%, -3.94%] n=274, 3 wk | 25.2% [20.4%, 30.6%] | 0.7% | - |
| volume_spurt_notice | 1d | 92 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 87 | not in B0 | 0 | - | - | - | 5.5% |
| volume_spurt_notice | 20d | 66 | not in B0 | 0 | - | - | - | 15.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 195 | -5.35% [-7.98%, -1.48%] n=195, 4 wk |
| mid | 435 | -3.91% [-5.29%, -1.95%] n=435, 4 wk |
| small | 649 | -5.28% [-6.63%, -2.53%] n=649, 4 wk |
| unknown | 43 | -0.35% [-12.26%, +5.44%] n=43, 3 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
