# Scoreboard v2 (shadow) — 2026-10-13 20:20Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 1175 total; visible 482 (graded at 5d 237, pending 245, ungradeable 0, no instrument 0); holdout 693 (sealed)
- Decisions: 4 total; visible 1; excluded 1; reaffirm 0
- Macro views: 19 total; visible graded 1

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 91 | typed_announcement | 33 | +0.62% [+0.62%, +0.62%] n=33, 1 wk | 60.6% [43.7%, 75.3%] | 0.7% | - |
| announcement | 5d | 91 | typed_announcement | 33 | +2.16% [+2.16%, +2.16%] n=33, 1 wk | 60.6% [43.7%, 75.3%] | 0.7% | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 3 | UP | 3 | -1.02% [-1.02%, -1.02%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.6% | - |
| block_deal_buy | 5d | 3 | UP | 3 | +0.78% [+0.78%, +0.78%] n=3, 1 wk | 66.7% [20.8%, 93.9%] | 0.6% | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 65 | UP | 65 | -0.88% [-0.88%, -0.88%] n=65, 1 wk | 40.0% [29.0%, 52.1%] | 0.7% | - |
| bulk_deal_buy | 5d | 65 | UP | 65 | +0.12% [+0.12%, +0.12%] n=65, 1 wk | 38.5% [27.6%, 50.6%] | 0.7% | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 3 | hint | 1 | +0.33% [+0.33%, +0.33%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.0% | - |
| macro_move | 5d | 3 | hint | 1 | +0.44% [+0.44%, +0.44%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.0% | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 5 | not in B0 | 0 | - | - | - | 2.1% |
| news_multi_source | 5d | 5 | not in B0 | 0 | - | - | - | 4.1% |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 3 | UP | 3 | +2.03% [+2.03%, +2.03%] n=3, 1 wk | 66.7% [20.8%, 93.9%] | 0.7% | - |
| sast_acquisition | 5d | 3 | UP | 3 | -0.98% [-0.98%, -0.98%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.7% | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 46 | hint | 46 | -0.79% [-0.79%, -0.79%] n=46, 1 wk | 54.3% [40.2%, 67.8%] | 0.7% | - |
| volume_breakout | 5d | 42 | hint | 42 | +0.06% [+0.06%, +0.06%] n=42, 1 wk | 52.4% [37.7%, 66.6%] | 0.7% | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 25 | not in B0 | 0 | - | - | - | 1.2% |
| volume_spurt_notice | 5d | 25 | not in B0 | 0 | - | - | - | 2.9% |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 11 | -1.17% [-1.17%, -1.17%] n=11, 1 wk |
| mid | 50 | -0.50% [-0.50%, -0.50%] n=50, 1 wk |
| small | 59 | +0.05% [+0.05%, +0.05%] n=59, 1 wk |
| unknown | 26 | +4.44% [+4.44%, +4.44%] n=26, 1 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
