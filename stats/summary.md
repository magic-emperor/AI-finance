# Scoreboard v2 (shadow) — 2026-11-16 23:29Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 3787 total; visible 1860 (graded at 5d 1335, pending 522, ungradeable 3, no instrument 0); holdout 1927 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 317 | typed_announcement | 106 | -0.87% [-2.24%, +0.56%] n=106, 4 wk | 39.6% [30.8%, 49.1%] | 0.7% | - |
| announcement | 5d | 279 | typed_announcement | 93 | -2.79% [-8.39%, +2.16%] n=93, 3 wk | 31.2% [22.7%, 41.2%] | 0.7% | - |
| announcement | 20d | 123 | typed_announcement | 47 | -10.49% [-14.18%, -8.92%] n=47, 2 wk | 10.6% [4.6%, 22.6%] | 0.7% | - |
| block_deal_buy | 1d | 18 | UP | 18 | -1.84% [-2.55%, -0.81%] n=18, 4 wk | 16.7% [5.8%, 39.2%] | 0.4% | - |
| block_deal_buy | 5d | 14 | UP | 14 | -1.92% [-3.14%, +0.78%] n=14, 3 wk | 28.6% [11.7%, 54.6%] | 0.4% | - |
| block_deal_buy | 20d | 4 | UP | 4 | -7.48% [-7.60%, -7.45%] n=4, 2 wk | 0.0% [0.0%, 49.0%] | 0.6% | - |
| bulk_deal_buy | 1d | 460 | UP | 460 | -2.02% [-2.35%, -1.15%] n=460, 4 wk | 31.3% [27.2%, 35.7%] | 0.6% | - |
| bulk_deal_buy | 5d | 412 | UP | 412 | -6.94% [-9.01%, -0.17%] n=412, 3 wk | 13.8% [10.8%, 17.5%] | 0.6% | - |
| bulk_deal_buy | 20d | 151 | UP | 151 | -15.53% [-19.86%, -8.38%] n=151, 2 wk | 18.5% [13.2%, 25.5%] | 0.7% | - |
| macro_move | 1d | 29 | hint | 27 | +0.21% [-0.05%, +0.42%] n=27, 4 wk | 63.0% [44.2%, 78.5%] | 0.0% | - |
| macro_move | 5d | 22 | hint | 20 | -0.40% [-1.15%, +0.44%] n=20, 3 wk | 60.0% [38.7%, 78.1%] | 0.0% | - |
| macro_move | 20d | 13 | hint | 11 | +2.88% [+0.87%, +3.08%] n=11, 2 wk | 63.6% [35.4%, 84.8%] | 0.0% | - |
| news_multi_source | 1d | 106 | not in B0 | 0 | - | - | - | 2.6% |
| news_multi_source | 5d | 95 | not in B0 | 0 | - | - | - | 5.8% |
| news_multi_source | 20d | 40 | not in B0 | 0 | - | - | - | 11.3% |
| sast_acquisition | 1d | 10 | UP | 10 | +0.36% [-0.70%, +2.03%] n=10, 3 wk | 60.0% [31.3%, 83.2%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 3 | UP | 3 | -2.35% [-2.35%, -2.35%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| volume_breakout | 1d | 529 | hint | 529 | -1.05% [-1.16%, -0.74%] n=529, 4 wk | 43.9% [39.7%, 48.1%] | 0.7% | - |
| volume_breakout | 5d | 435 | hint | 435 | -3.12% [-4.32%, +0.01%] n=435, 3 wk | 34.9% [30.6%, 39.5%] | 0.7% | - |
| volume_breakout | 20d | 117 | hint | 117 | -9.39% [-12.91%, -3.94%] n=117, 2 wk | 29.9% [22.4%, 38.7%] | 0.7% | - |
| volume_spurt_notice | 1d | 79 | not in B0 | 0 | - | - | - | 2.0% |
| volume_spurt_notice | 5d | 68 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 46 | not in B0 | 0 | - | - | - | 15.4% |

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
