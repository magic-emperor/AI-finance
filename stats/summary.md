# Scoreboard v2 (shadow) — 2026-11-20 21:56Z

Grader grader_v2.0. Visible weeks only. Holdout weeks are sealed; only their counts are shown.
Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**

## Coverage

- Flags (opportunities): 4285 total; visible 1860 (graded at 5d 1610, pending 247, ungradeable 3, no instrument 0); holdout 2425 (sealed)
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
| announcement | 5d | 319 | typed_announcement | 108 | -2.74% [-7.14%, +1.14%] n=108, 4 wk | 34.3% [26.0%, 43.6%] | 0.7% | - |
| announcement | 20d | 182 | typed_announcement | 61 | -12.34% [-16.36%, -8.92%] n=61, 2 wk | 9.8% [4.6%, 19.8%] | 0.7% | - |
| block_deal_buy | 1d | 41 | UP | 41 | -0.83% [-2.55%, -0.22%] n=41, 4 wk | 63.4% [48.1%, 76.4%] | 0.4% | - |
| block_deal_buy | 5d | 18 | UP | 18 | -2.45% [-3.78%, +0.29%] n=18, 4 wk | 22.2% [9.0%, 45.2%] | 0.4% | - |
| block_deal_buy | 20d | 5 | UP | 5 | -4.93% [-7.45%, -1.17%] n=5, 2 wk | 20.0% [3.6%, 62.4%] | 0.5% | - |
| bulk_deal_buy | 1d | 612 | UP | 612 | -2.14% [-2.57%, -1.33%] n=612, 4 wk | 31.9% [28.3%, 35.7%] | 0.7% | - |
| bulk_deal_buy | 5d | 510 | UP | 510 | -6.84% [-9.06%, -1.87%] n=510, 4 wk | 15.7% [12.8%, 19.1%] | 0.7% | - |
| bulk_deal_buy | 20d | 336 | UP | 336 | -16.35% [-18.12%, -8.98%] n=336, 2 wk | 10.1% [7.3%, 13.8%] | 0.7% | - |
| macro_move | 1d | 32 | hint | 30 | +0.21% [+0.02%, +0.42%] n=30, 4 wk | 66.7% [48.8%, 80.8%] | 0.0% | - |
| macro_move | 5d | 29 | hint | 27 | -0.17% [-0.95%, +0.49%] n=27, 4 wk | 63.0% [44.2%, 78.5%] | 0.0% | - |
| macro_move | 20d | 15 | hint | 13 | +3.07% [+0.87%, +3.25%] n=13, 2 wk | 61.5% [35.5%, 82.3%] | 0.0% | - |
| news_multi_source | 1d | 123 | not in B0 | 0 | - | - | - | 2.4% |
| news_multi_source | 5d | 115 | not in B0 | 0 | - | - | - | 5.6% |
| news_multi_source | 20d | 66 | not in B0 | 0 | - | - | - | 10.0% |
| sast_acquisition | 1d | 16 | UP | 16 | -0.07% [-0.77%, +1.35%] n=16, 4 wk | 56.2% [33.2%, 76.9%] | 0.7% | - |
| sast_acquisition | 5d | 10 | UP | 10 | -6.87% [-11.96%, -0.98%] n=10, 3 wk | 10.0% [1.8%, 40.4%] | 0.7% | - |
| sast_acquisition | 20d | 4 | UP | 4 | -7.17% [-21.63%, -2.35%] n=4, 2 wk | 25.0% [4.6%, 69.9%] | 0.6% | - |
| volume_breakout | 1d | 596 | hint | 596 | -1.15% [-1.21%, -1.02%] n=596, 4 wk | 42.4% [38.5%, 46.5%] | 0.7% | - |
| volume_breakout | 5d | 529 | hint | 529 | -3.26% [-4.20%, -1.59%] n=529, 4 wk | 33.1% [29.2%, 37.2%] | 0.7% | - |
| volume_breakout | 20d | 240 | hint | 240 | -10.58% [-12.15%, -3.94%] n=240, 2 wk | 24.2% [19.2%, 30.0%] | 0.7% | - |
| volume_spurt_notice | 1d | 89 | not in B0 | 0 | - | - | - | 2.1% |
| volume_spurt_notice | 5d | 80 | not in B0 | 0 | - | - | - | 5.2% |
| volume_spurt_notice | 20d | 65 | not in B0 | 0 | - | - | - | 15.5% |

## B0 by liquidity bucket (5d, NSE equities)

| bucket | n | B0 net (95% week-block CI) |
|---|---|---|
| large | 175 | -6.52% [-8.71%, -2.00%] n=175, 4 wk |
| mid | 392 | -4.10% [-5.39%, -1.95%] n=392, 4 wk |
| small | 553 | -4.90% [-6.67%, -1.58%] n=553, 4 wk |
| unknown | 55 | -3.06% [-14.33%, +4.91%] n=55, 4 wk |

## Macro views (calibration only, never playbook evidence)

| market | n | Brier | climatology Brier | skill vs climatology |
|---|---|---|---|---|
| USDINR=X | 1 | 0.194 | 0.130 | -0.486 |
| ALL | 1 | 0.194 | 0.130 | -0.486 |

## Excluded (shown, never counted)

- `agent-20261002T034124Z-001` USDINR=X UP p=0.56: forced by the old step 3b (any market view with p >= 0.55 had to be recorded as a call); not the agent's judgment

## Not in this version

B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3).
