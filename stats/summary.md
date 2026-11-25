# Scoreboard v2 (shadow) — 2026-11-25 23:17Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 4559 total; visible 2133 (graded at 5d 1854, pending 273, ungradeable 6, no instrument 0); holdout 2426 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 348 | typed_announcement | 116 | -1.07% [-2.32%, +0.07%] n=116, 4 wk | 37.9% [29.6%, 47.0%] | 0.7% | - |
| announcement | 5d | 348 | typed_announcement | 116 | -2.97% [-7.14%, +0.85%] n=116, 4 wk | 32.8% [24.9%, 41.7%] | 0.7% | - |
| announcement | 20d | 182 | typed_announcement | 61 | -12.34% [-16.36%, -8.92%] n=61, 2 wk | 9.8% [4.6%, 19.8%] | 0.7% | - |
| block_deal_buy | 1d | 41 | UP | 41 | -0.83% [-2.55%, -0.22%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 5d | 41 | UP | 41 | -0.99% [-2.75%, +0.29%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 20d | 5 | UP | 5 | -4.93% [-7.45%, -1.17%] n=5, 2 wk | 20.0% [3.6%, 62.4%] | 0.5% | - |
| bulk_deal_buy | 1d | 611 | UP | 611 | -2.15% [-2.57%, -1.34%] n=611, 4 wk | 31.8% [28.2%, 35.5%] | 0.7% | - |
| bulk_deal_buy | 5d | 611 | UP | 611 | -6.81% [-8.98%, -2.53%] n=611, 4 wk | 15.2% [12.6%, 18.3%] | 0.7% | - |
| bulk_deal_buy | 20d | 335 | UP | 335 | -16.33% [-18.12%, -8.75%] n=335, 2 wk | 10.1% [7.4%, 13.8%] | 0.7% | - |
| macro_move | 1d | 32 | hint | 30 | +0.21% [+0.02%, +0.42%] n=30, 4 wk | 66.7% [48.8%, 80.8%] | 0.0% | - |
| macro_move | 5d | 32 | hint | 30 | -0.12% [-0.95%, +0.45%] n=30, 4 wk | 63.3% [45.5%, 78.1%] | 0.0% | - |
| macro_move | 20d | 17 | hint | 15 | +2.63% [-0.20%, +3.25%] n=15, 3 wk | 60.0% [35.7%, 80.2%] | 0.0% | - |
| news_multi_source | 1d | 123 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 123 | not in B0 | 0 | - | - | - | 5.5% |
| news_multi_source | 20d | 66 | not in B0 | 0 | - | - | - | 10.0% |
| sast_acquisition | 1d | 16 | UP | 16 | -0.07% [-0.77%, +1.35%] n=16, 4 wk | 56.2% [33.2%, 76.9%] | 0.7% | - |
| sast_acquisition | 5d | 16 | UP | 16 | -4.54% [-9.40%, -0.77%] n=16, 4 wk | 31.2% [14.2%, 55.6%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 595 | hint | 595 | -1.15% [-1.21%, -1.02%] n=595, 4 wk | 42.4% [38.4%, 46.4%] | 0.7% | - |
| volume_breakout | 5d | 594 | hint | 594 | -3.32% [-4.10%, -2.05%] n=594, 4 wk | 32.2% [28.5%, 36.0%] | 0.7% | - |
| volume_breakout | 20d | 240 | hint | 240 | -10.58% [-12.15%, -3.94%] n=240, 2 wk | 24.2% [19.2%, 30.0%] | 0.7% | - |
| volume_spurt_notice | 1d | 89 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 89 | not in B0 | 0 | - | - | - | 5.4% |
| volume_spurt_notice | 20d | 65 | not in B0 | 0 | - | - | - | 15.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 209 | -5.65% [-8.30%, -1.55%] n=209, 4 wk |
| mid | 439 | -3.89% [-5.29%, -1.97%] n=439, 4 wk |
| small | 656 | -5.26% [-6.67%, -2.53%] n=656, 4 wk |
| unknown | 74 | -3.35% [-12.50%, +3.51%] n=74, 4 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
