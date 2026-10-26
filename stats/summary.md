# Scoreboard v2 (shadow) — 2026-10-26 20:07Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 2098 total; visible 1001 (graded at 5d 651, pending 350, ungradeable 0, no instrument 0); holdout 1097 (sealed)
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
| announcement | 5d | 143 | typed_announcement | 51 | -1.67% [-8.69%, +2.16%] n=51, 2 wk | 39.2% [27.0%, 52.9%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 5 | UP | 5 | -0.87% [-1.02%, -0.65%] n=5, 2 wk | 40.0% [11.8%, 76.9%] | 0.5% | - |
| block_deal_buy | 5d | 4 | UP | 4 | +0.00% [-2.33%, +0.78%] n=4, 2 wk | 50.0% [15.0%, 85.0%] | 0.6% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 336 | UP | 336 | -2.35% [-2.71%, -0.88%] n=336, 2 wk | 29.5% [24.8%, 34.5%] | 0.7% | - |
| bulk_deal_buy | 5d | 225 | UP | 225 | -6.47% [-9.14%, +0.12%] n=225, 2 wk | 16.4% [12.2%, 21.8%] | 0.6% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 15 | hint | 13 | +0.42% [+0.33%, +0.43%] n=13, 2 wk | 69.2% [42.4%, 87.3%] | 0.0% | - |
| macro_move | 5d | 14 | hint | 12 | +0.01% [-0.03%, +0.44%] n=12, 2 wk | 83.3% [55.2%, 95.3%] | 0.0% | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 66 | not in B0 | 0 | - | - | - | 3.0% |
| news_multi_source | 5d | 52 | not in B0 | 0 | - | - | - | 7.0% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 4 | UP | 4 | +1.35% [-0.70%, +2.03%] n=4, 2 wk | 50.0% [15.0%, 85.0%] | 0.6% | - |
| sast_acquisition | 5d | 4 | UP | 4 | -3.73% [-11.96%, -0.98%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 240 | hint | 240 | -1.07% [-1.13%, -0.79%] n=240, 2 wk | 46.2% [40.1%, 52.6%] | 0.7% | - |
| volume_breakout | 5d | 157 | hint | 157 | -3.25% [-4.60%, +0.01%] n=157, 2 wk | 34.4% [27.4%, 42.1%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 65 | not in B0 | 0 | - | - | - | 2.2% |
| volume_spurt_notice | 5d | 52 | not in B0 | 0 | - | - | - | 4.9% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 54 | -8.38% [-10.22%, -1.17%] n=54, 2 wk |
| mid | 166 | -4.80% [-6.66%, -0.50%] n=166, 2 wk |
| small | 183 | -4.07% [-6.21%, +0.02%] n=183, 2 wk |
| unknown | 38 | -1.87% [-15.55%, +4.44%] n=38, 2 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
