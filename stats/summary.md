# Scoreboard v2 (shadow) — 2026-10-30 19:40Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 2486 total; visible 1389 (graded at 5d 913, pending 476, ungradeable 0, no instrument 0); holdout 1097 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 222 | typed_announcement | 80 | -0.78% [-2.69%, +0.62%] n=80, 3 wk | 38.8% [28.8%, 49.7%] | 0.7% | - |
| announcement | 5d | 182 | typed_announcement | 61 | -2.88% [-8.83%, +2.16%] n=61, 2 wk | 34.4% [23.7%, 47.0%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 5 | UP | 5 | -0.87% [-1.02%, -0.65%] n=5, 2 wk | 40.0% [11.8%, 76.9%] | 0.5% | - |
| block_deal_buy | 5d | 5 | UP | 5 | +0.29% [-0.45%, +0.78%] n=5, 2 wk | 60.0% [23.1%, 88.2%] | 0.5% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 379 | UP | 379 | -2.15% [-2.71%, -0.55%] n=379, 3 wk | 31.4% [26.9%, 36.2%] | 0.7% | - |
| bulk_deal_buy | 5d | 336 | UP | 336 | -7.71% [-9.59%, +0.12%] n=336, 2 wk | 11.6% [8.6%, 15.5%] | 0.7% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 18 | hint | 16 | +0.32% [-0.13%, +0.43%] n=16, 3 wk | 62.5% [38.6%, 81.5%] | 0.0% | - |
| macro_move | 5d | 15 | hint | 13 | +0.00% [-0.04%, +0.44%] n=13, 2 wk | 76.9% [49.7%, 91.8%] | 0.0% | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 87 | not in B0 | 0 | - | - | - | 2.7% |
| news_multi_source | 5d | 66 | not in B0 | 0 | - | - | - | 6.5% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 6 | UP | 6 | +0.87% [-0.70%, +2.03%] n=6, 3 wk | 66.7% [30.0%, 90.3%] | 0.7% | - |
| sast_acquisition | 5d | 4 | UP | 4 | -3.73% [-11.96%, -0.98%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 319 | hint | 319 | -1.19% [-1.56%, -0.79%] n=319, 3 wk | 42.9% [37.6%, 48.4%] | 0.7% | - |
| volume_breakout | 5d | 240 | hint | 240 | -3.49% [-4.32%, +0.01%] n=240, 2 wk | 33.8% [28.1%, 39.9%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 68 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 65 | not in B0 | 0 | - | - | - | 5.2% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 92 | -8.19% [-9.14%, -1.17%] n=92, 2 wk |
| mid | 202 | -4.50% [-5.81%, -0.50%] n=202, 2 wk |
| small | 307 | -5.76% [-7.25%, +0.02%] n=307, 2 wk |
| unknown | 45 | -4.18% [-15.98%, +4.44%] n=45, 2 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
