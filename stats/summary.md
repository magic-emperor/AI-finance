# Scoreboard v2 (shadow) — 2026-11-18 22:18Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 4048 total; visible 1860 (graded at 5d 1363, pending 494, ungradeable 3, no instrument 0); holdout 2188 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 346 | typed_announcement | 114 | -1.01% [-2.29%, +0.11%] n=114, 4 wk | 38.6% [30.2%, 47.8%] | 0.7% | - |
| announcement | 5d | 292 | typed_announcement | 100 | -2.63% [-6.88%, +1.69%] n=100, 4 wk | 33.0% [24.6%, 42.7%] | 0.7% | - |
| announcement | 20d | 169 | typed_announcement | 56 | -11.68% [-15.63%, -8.92%] n=56, 2 wk | 10.7% [5.0%, 21.5%] | 0.7% | - |
| block_deal_buy | 1d | 41 | UP | 41 | -0.83% [-2.55%, -0.22%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 5d | 14 | UP | 14 | -1.92% [-3.14%, +0.78%] n=14, 3 wk | 28.6% [11.7%, 54.6%] | 0.4% | - |
| block_deal_buy | 20d | 5 | UP | 5 | -4.93% [-7.45%, -1.17%] n=5, 2 wk | 20.0% [3.6%, 62.4%] | 0.5% | - |
| bulk_deal_buy | 1d | 559 | UP | 559 | -2.01% [-2.35%, -1.28%] n=559, 4 wk | 33.5% [29.7%, 37.5%] | 0.7% | - |
| bulk_deal_buy | 5d | 412 | UP | 412 | -6.94% [-9.01%, -0.17%] n=412, 3 wk | 13.8% [10.8%, 17.5%] | 0.6% | - |
| bulk_deal_buy | 20d | 263 | UP | 263 | -16.27% [-18.46%, -8.38%] n=263, 2 wk | 12.5% [9.1%, 17.1%] | 0.7% | - |
| macro_move | 1d | 32 | hint | 30 | +0.21% [+0.02%, +0.42%] n=30, 4 wk | 66.7% [48.8%, 80.8%] | 0.0% | - |
| macro_move | 5d | 25 | hint | 23 | -0.34% [-1.00%, +0.15%] n=23, 4 wk | 60.9% [40.8%, 77.8%] | 0.0% | - |
| macro_move | 20d | 15 | hint | 13 | +3.07% [+0.87%, +3.25%] n=13, 2 wk | 61.5% [35.5%, 82.3%] | 0.0% | - |
| news_multi_source | 1d | 122 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 106 | not in B0 | 0 | - | - | - | 5.8% |
| news_multi_source | 20d | 61 | not in B0 | 0 | - | - | - | 10.4% |
| sast_acquisition | 1d | 15 | UP | 15 | -0.08% [-0.91%, +1.35%] n=15, 4 wk | 53.3% [30.1%, 75.2%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 594 | hint | 594 | -1.16% [-1.22%, -1.02%] n=594, 4 wk | 42.4% [38.5%, 46.4%] | 0.7% | - |
| volume_breakout | 5d | 435 | hint | 435 | -3.12% [-4.32%, +0.01%] n=435, 3 wk | 34.9% [30.6%, 39.5%] | 0.7% | - |
| volume_breakout | 20d | 193 | hint | 193 | -9.75% [-11.57%, -3.94%] n=193, 2 wk | 26.4% [20.7%, 33.1%] | 0.7% | - |
| volume_spurt_notice | 1d | 87 | not in B0 | 0 | - | - | - | 2.2% |
| volume_spurt_notice | 5d | 69 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 62 | not in B0 | 0 | - | - | - | 15.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 148 | -6.56% [-8.64%, -1.17%] n=148, 4 wk |
| mid | 343 | -4.08% [-5.76%, -0.55%] n=343, 4 wk |
| small | 458 | -4.92% [-7.01%, +0.05%] n=458, 4 wk |
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
