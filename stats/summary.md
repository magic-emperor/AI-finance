# Scoreboard v2 (shadow) — 2026-10-27 19:36Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 2191 total; visible 1094 (graded at 5d 810, pending 283, ungradeable 1, no instrument 0); holdout 1097 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 182 | typed_announcement | 61 | -0.90% [-2.69%, +0.62%] n=61, 2 wk | 36.1% [25.2%, 48.6%] | 0.7% | - |
| announcement | 5d | 170 | typed_announcement | 57 | -2.68% [-9.34%, +2.16%] n=57, 2 wk | 35.1% [24.0%, 48.1%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 5 | UP | 5 | -0.87% [-1.02%, -0.65%] n=5, 2 wk | 40.0% [11.8%, 76.9%] | 0.5% | - |
| block_deal_buy | 5d | 5 | UP | 5 | +0.29% [-0.45%, +0.78%] n=5, 2 wk | 60.0% [23.1%, 88.2%] | 0.5% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 336 | UP | 336 | -2.35% [-2.71%, -0.88%] n=336, 2 wk | 29.5% [24.8%, 34.5%] | 0.7% | - |
| bulk_deal_buy | 5d | 299 | UP | 299 | -7.35% [-9.43%, +0.12%] n=299, 2 wk | 13.0% [9.7%, 17.3%] | 0.7% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 15 | hint | 13 | +0.42% [+0.33%, +0.43%] n=13, 2 wk | 69.2% [42.4%, 87.3%] | 0.0% | - |
| macro_move | 5d | 15 | hint | 13 | +0.00% [-0.04%, +0.44%] n=13, 2 wk | 76.9% [49.7%, 91.8%] | 0.0% | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 66 | not in B0 | 0 | - | - | - | 3.0% |
| news_multi_source | 5d | 62 | not in B0 | 0 | - | - | - | 6.8% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 4 | UP | 4 | +1.35% [-0.70%, +2.03%] n=4, 2 wk | 50.0% [15.0%, 85.0%] | 0.6% | - |
| sast_acquisition | 5d | 4 | UP | 4 | -3.73% [-11.96%, -0.98%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 240 | hint | 240 | -1.07% [-1.13%, -0.79%] n=240, 2 wk | 46.2% [40.1%, 52.6%] | 0.7% | - |
| volume_breakout | 5d | 193 | hint | 193 | -3.27% [-4.29%, +0.01%] n=193, 2 wk | 34.7% [28.4%, 41.7%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 65 | not in B0 | 0 | - | - | - | 2.2% |
| volume_spurt_notice | 5d | 62 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 81 | -8.39% [-9.52%, -1.17%] n=81, 2 wk |
| mid | 186 | -4.47% [-5.94%, -0.50%] n=186, 2 wk |
| small | 246 | -5.27% [-7.09%, +0.02%] n=246, 2 wk |
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
