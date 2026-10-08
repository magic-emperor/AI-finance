# Scoreboard v2 (shadow) — 2026-10-08 20:06Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 805 total; visible 241 (graded at 5d 0, pending 241, ungradeable 0, no instrument 0); holdout 564 (sealed)
- Decisions: 2 total; visible 1; excluded 1; reaffirm 0
- Macro views: 13 total; visible graded 0

## Champion vs mechanical baseline (B0)

| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| (no graded visible decisions yet) | | | | | | | | | | |

## Every flag, by kind: B0 = each flag in its kind's default direction

| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |
|---|---|---|---|---|---|---|---|---|
| announcement | 1d | 30 | typed_announcement | 12 | +0.22% [+0.22%, +0.22%] n=12, 1 wk | 66.7% [39.1%, 86.2%] | 0.7% | - |
| announcement | 5d | 0 | typed_announcement | 0 | - | - | - | - |
| announcement | 20d | 0 | typed_announcement | 0 | - | - | - | - |
| block_deal_buy | 1d | 2 | UP | 2 | -0.89% [-0.89%, -0.89%] n=2, 1 wk | 50.0% [9.5%, 90.5%] | 0.7% | - |
| block_deal_buy | 5d | 0 | UP | 0 | - | - | - | - |
| block_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 1d | 3 | UP | 3 | -1.47% [-1.47%, -1.47%] n=3, 1 wk | 33.3% [6.1%, 79.2%] | 0.8% | - |
| bulk_deal_buy | 5d | 0 | UP | 0 | - | - | - | - |
| bulk_deal_buy | 20d | 0 | UP | 0 | - | - | - | - |
| macro_move | 1d | 3 | hint | 1 | +0.33% [+0.33%, +0.33%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.0% | - |
| macro_move | 5d | 0 | hint | 0 | - | - | - | - |
| macro_move | 20d | 0 | hint | 0 | - | - | - | - |
| news_multi_source | 1d | 0 | not in B0 | 0 | - | - | - | - |
| news_multi_source | 5d | 0 | not in B0 | 0 | - | - | - | - |
| news_multi_source | 20d | 0 | not in B0 | 0 | - | - | - | - |
| sast_acquisition | 1d | 1 | UP | 1 | +6.65% [+6.65%, +6.65%] n=1, 1 wk | 100.0% [20.7%, 100.0%] | 0.8% | - |
| sast_acquisition | 5d | 0 | UP | 0 | - | - | - | - |
| sast_acquisition | 20d | 0 | UP | 0 | - | - | - | - |
| volume_breakout | 1d | 25 | hint | 25 | -0.75% [-0.75%, -0.75%] n=25, 1 wk | 60.0% [40.7%, 76.6%] | 0.7% | - |
| volume_breakout | 5d | 0 | hint | 0 | - | - | - | - |
| volume_breakout | 20d | 0 | hint | 0 | - | - | - | - |
| volume_spurt_notice | 1d | 10 | not in B0 | 0 | - | - | - | 0.7% |
| volume_spurt_notice | 5d | 0 | not in B0 | 0 | - | - | - | - |
| volume_spurt_notice | 20d | 0 | not in B0 | 0 | - | - | - | - |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| (none graded yet) | | |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| (none graded yet) | | | | |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
