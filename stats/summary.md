# Scoreboard v2 (shadow) — 2026-11-17 22:37Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 3940 total; visible 1860 (graded at 5d 1393, pending 464, ungradeable 3, no instrument 0); holdout 2080 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 335 | typed_announcement | 114 | -1.08% [-2.35%, +0.07%] n=114, 4 wk | 37.7% [29.4%, 46.9%] | 0.7% | - |
| announcement | 5d | 281 | typed_announcement | 95 | -2.91% [-8.83%, +2.16%] n=95, 3 wk | 31.6% [23.1%, 41.5%] | 0.7% | - |
| announcement | 20d | 143 | typed_announcement | 51 | -11.52% [-16.27%, -8.92%] n=51, 2 wk | 9.8% [4.3%, 21.0%] | 0.7% | - |
| block_deal_buy | 1d | 19 | UP | 19 | -1.62% [-2.55%, -0.45%] n=19, 4 wk | 21.1% [8.5%, 43.3%] | 0.4% | - |
| block_deal_buy | 5d | 14 | UP | 14 | -1.92% [-3.14%, +0.78%] n=14, 3 wk | 28.6% [11.7%, 54.6%] | 0.4% | - |
| block_deal_buy | 20d | 4 | UP | 4 | -7.48% [-7.60%, -7.45%] n=4, 2 wk | 0.0% [0.0%, 49.0%] | 0.6% | - |
| bulk_deal_buy | 1d | 559 | UP | 559 | -2.32% [-2.65%, -1.44%] n=559, 4 wk | 28.3% [24.7%, 32.1%] | 0.7% | - |
| bulk_deal_buy | 5d | 457 | UP | 457 | -7.13% [-9.59%, +0.12%] n=457, 3 wk | 14.7% [11.7%, 18.2%] | 0.6% | - |
| bulk_deal_buy | 20d | 225 | UP | 225 | -16.22% [-19.17%, -8.98%] n=225, 2 wk | 13.8% [9.9%, 18.9%] | 0.6% | - |
| macro_move | 1d | 32 | hint | 30 | +0.21% [+0.02%, +0.42%] n=30, 4 wk | 66.7% [48.8%, 80.8%] | 0.0% | - |
| macro_move | 5d | 24 | hint | 22 | -0.27% [-0.96%, +0.87%] n=22, 4 wk | 63.6% [43.0%, 80.3%] | 0.0% | - |
| macro_move | 20d | 15 | hint | 13 | +3.07% [+0.87%, +3.25%] n=13, 2 wk | 61.5% [35.5%, 82.3%] | 0.0% | - |
| news_multi_source | 1d | 121 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 104 | not in B0 | 0 | - | - | - | 6.0% |
| news_multi_source | 20d | 52 | not in B0 | 0 | - | - | - | 10.6% |
| sast_acquisition | 1d | 12 | UP | 12 | +0.32% [-0.36%, +1.69%] n=12, 4 wk | 58.3% [32.0%, 80.7%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 560 | hint | 560 | -1.15% [-1.22%, -1.02%] n=560, 4 wk | 42.9% [38.8%, 47.0%] | 0.7% | - |
| volume_breakout | 5d | 435 | hint | 435 | -3.12% [-4.32%, +0.01%] n=435, 3 wk | 34.9% [30.6%, 39.5%] | 0.7% | - |
| volume_breakout | 20d | 157 | hint | 157 | -10.48% [-13.19%, -3.94%] n=157, 2 wk | 25.5% [19.3%, 32.8%] | 0.7% | - |
| volume_spurt_notice | 1d | 88 | not in B0 | 0 | - | - | - | 2.2% |
| volume_spurt_notice | 5d | 68 | not in B0 | 0 | - | - | - | 5.1% |
| volume_spurt_notice | 20d | 52 | not in B0 | 0 | - | - | - | 15.2% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 158 | -6.95% [-9.14%, -1.17%] n=158, 3 wk |
| mid | 341 | -4.09% [-5.81%, -0.50%] n=341, 3 wk |
| small | 458 | -5.04% [-7.25%, +0.02%] n=458, 3 wk |
| unknown | 54 | -3.43% [-15.98%, +4.44%] n=54, 3 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
