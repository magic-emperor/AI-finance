# Scoreboard v2 (shadow) — 2026-11-04 20:54Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 2904 total; visible 1392 (graded at 5d 976, pending 416, ungradeable 0, no instrument 0); holdout 1512 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 279 | typed_announcement | 93 | -1.05% [-2.66%, +0.62%] n=93, 3 wk | 35.5% [26.5%, 45.6%] | 0.7% | - |
| announcement | 5d | 209 | typed_announcement | 74 | -2.50% [-8.39%, +2.16%] n=74, 3 wk | 35.1% [25.2%, 46.5%] | 0.7% | - |
| announcement | 20d | 91 | typed_announcement | 33 | -8.92% [-8.92%, -8.92%] n=33, 1 wk | 15.2% [6.7%, 30.9%] | 0.7% | - |
| block_deal_buy | 1d | 14 | UP | 14 | -2.06% [-2.73%, -0.65%] n=14, 3 wk | 14.3% [4.0%, 39.9%] | 0.4% | - |
| block_deal_buy | 5d | 5 | UP | 5 | +0.29% [-0.45%, +0.78%] n=5, 2 wk | 60.0% [23.1%, 88.2%] | 0.5% | - |
| block_deal_buy | 20d | 3 | UP | 3 | -7.45% [-7.45%, -7.45%] n=3, 1 wk | 0.0% [0.0%, 56.2%] | 0.6% | - |
| bulk_deal_buy | 1d | 412 | UP | 412 | -2.03% [-2.42%, -0.69%] n=412, 3 wk | 31.8% [27.5%, 36.4%] | 0.6% | - |
| bulk_deal_buy | 5d | 322 | UP | 322 | -7.26% [-9.01%, -0.17%] n=322, 3 wk | 12.4% [9.3%, 16.5%] | 0.6% | - |
| bulk_deal_buy | 20d | 57 | UP | 57 | -8.38% [-8.38%, -8.38%] n=57, 1 wk | 36.8% [25.5%, 49.8%] | 0.7% | - |
| macro_move | 1d | 22 | hint | 20 | +0.32% [+0.15%, +0.43%] n=20, 3 wk | 65.0% [43.3%, 81.9%] | 0.0% | - |
| macro_move | 5d | 17 | hint | 15 | -0.02% [-0.15%, +0.44%] n=15, 3 wk | 73.3% [48.0%, 89.1%] | 0.0% | - |
| macro_move | 20d | 3 | hint | 1 | +0.87% [+0.87%, +0.87%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.0% | - |
| news_multi_source | 1d | 95 | not in B0 | 0 | - | - | - | 2.7% |
| news_multi_source | 5d | 79 | not in B0 | 0 | - | - | - | 6.1% |
| news_multi_source | 20d | 5 | not in B0 | 0 | - | - | - | 11.5% |
| sast_acquisition | 1d | 10 | UP | 10 | +0.36% [-0.70%, +2.03%] n=10, 3 wk | 60.0% [31.3%, 83.2%] | 0.7% | - |
| sast_acquisition | 5d | 4 | UP | 4 | -3.73% [-11.96%, -0.98%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| sast_acquisition | 20d | 3 | UP | 3 | -2.35% [-2.35%, -2.35%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| volume_breakout | 1d | 436 | hint | 436 | -1.12% [-1.18%, -0.79%] n=436, 3 wk | 44.7% [40.1%, 49.4%] | 0.7% | - |
| volume_breakout | 5d | 274 | hint | 274 | -3.53% [-4.32%, +0.01%] n=274, 3 wk | 34.7% [29.3%, 40.5%] | 0.7% | - |
| volume_breakout | 20d | 46 | hint | 46 | -3.94% [-3.94%, -3.94%] n=46, 1 wk | 47.8% [34.1%, 61.9%] | 0.7% | - |
| volume_spurt_notice | 1d | 68 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 66 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 25 | not in B0 | 0 | - | - | - | 12.1% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 98 | -7.22% [-8.85%, -1.17%] n=98, 3 wk |
| mid | 227 | -4.39% [-5.81%, -0.50%] n=227, 3 wk |
| small | 332 | -5.57% [-7.11%, +0.02%] n=332, 3 wk |
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
