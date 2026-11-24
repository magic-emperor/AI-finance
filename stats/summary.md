# Scoreboard v2 (shadow) — 2026-11-24 01:52Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 4395 total; visible 1969 (graded at 5d 1725, pending 241, ungradeable 3, no instrument 0); holdout 2426 (sealed)
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
| announcement | 5d | 335 | typed_announcement | 114 | -3.05% [-7.14%, +0.85%] n=114, 4 wk | 32.5% [24.6%, 41.5%] | 0.7% | - |
| announcement | 20d | 182 | typed_announcement | 61 | -12.34% [-16.36%, -8.92%] n=61, 2 wk | 9.8% [4.6%, 19.8%] | 0.7% | - |
| block_deal_buy | 1d | 41 | UP | 41 | -0.83% [-2.55%, -0.22%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 5d | 19 | UP | 19 | -2.36% [-3.30%, +0.29%] n=19, 4 wk | 21.1% [8.5%, 43.3%] | 0.4% | - |
| block_deal_buy | 20d | 5 | UP | 5 | -4.93% [-7.45%, -1.17%] n=5, 2 wk | 20.0% [3.6%, 62.4%] | 0.5% | - |
| bulk_deal_buy | 1d | 612 | UP | 612 | -2.14% [-2.57%, -1.33%] n=612, 4 wk | 31.9% [28.3%, 35.7%] | 0.7% | - |
| bulk_deal_buy | 5d | 559 | UP | 559 | -7.10% [-9.06%, -2.32%] n=559, 4 wk | 15.0% [12.3%, 18.2%] | 0.7% | - |
| bulk_deal_buy | 20d | 336 | UP | 336 | -16.35% [-18.12%, -8.98%] n=336, 2 wk | 10.1% [7.3%, 13.8%] | 0.7% | - |
| macro_move | 1d | 32 | hint | 30 | +0.21% [+0.02%, +0.42%] n=30, 4 wk | 66.7% [48.8%, 80.8%] | 0.0% | - |
| macro_move | 5d | 32 | hint | 30 | -0.12% [-0.95%, +0.45%] n=30, 4 wk | 63.3% [45.5%, 78.1%] | 0.0% | - |
| macro_move | 20d | 16 | hint | 14 | +2.75% [-1.34%, +3.25%] n=14, 3 wk | 57.1% [32.6%, 78.6%] | 0.0% | - |
| news_multi_source | 1d | 123 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 121 | not in B0 | 0 | - | - | - | 5.5% |
| news_multi_source | 20d | 66 | not in B0 | 0 | - | - | - | 10.0% |
| sast_acquisition | 1d | 16 | UP | 16 | -0.07% [-0.77%, +1.35%] n=16, 4 wk | 56.2% [33.2%, 76.9%] | 0.7% | - |
| sast_acquisition | 5d | 12 | UP | 12 | -5.80% [-9.40%, -0.76%] n=12, 4 wk | 16.7% [4.7%, 44.8%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 596 | hint | 596 | -1.15% [-1.21%, -1.02%] n=596, 4 wk | 42.4% [38.5%, 46.5%] | 0.7% | - |
| volume_breakout | 5d | 559 | hint | 559 | -3.35% [-4.26%, -1.97%] n=559, 4 wk | 32.4% [28.6%, 36.4%] | 0.7% | - |
| volume_breakout | 20d | 240 | hint | 240 | -10.58% [-12.15%, -3.94%] n=240, 2 wk | 24.2% [19.2%, 30.0%] | 0.7% | - |
| volume_spurt_notice | 1d | 89 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 88 | not in B0 | 0 | - | - | - | 5.4% |
| volume_spurt_notice | 20d | 65 | not in B0 | 0 | - | - | - | 15.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 179 | -6.61% [-8.74%, -3.07%] n=179, 4 wk |
| mid | 415 | -3.93% [-5.29%, -1.95%] n=415, 4 wk |
| small | 612 | -5.39% [-6.93%, -2.53%] n=612, 4 wk |
| unknown | 57 | -3.35% [-14.06%, +4.02%] n=57, 4 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
