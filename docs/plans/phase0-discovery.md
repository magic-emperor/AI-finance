# Phase 0 discovery report: prediction agent v2

| | |
|---|---|
| Written | 2026-10-02, about 07:40 UTC, by Claude Code |
| Plan | `docs/plans/prediction-agent-v2-plan.md` (v1), §6.8 |
| Branch | `claude/phase-0-discovery`, PR into `feat/liquidity-core-v2` |
| Status | **Phase 0 stops here.** Nothing in Phase 1 has been started. |

All numbers below come from the live branches (`agent-data`, `claude/agent-ledger`), the routine's run logs, and the GitHub Actions run list, read today. The data covers only **about 15 hours of live operation** (the first scout run was 2026-10-01 16:56 UTC), and **2 Oct was an NSE holiday** (Gandhi Jayanti). Treat every volume figure as a first impression, not a rate.

---

## 0. What this PR contains, and where it deviates from the plan

| File | What it is |
|---|---|
| `agent/RUNBOOK.md` | The live routine prompt with the §14.2 edits applied (§6.5–6.7). **Not live yet**; see "Switching to the thin loader" below. |
| `tools/evidence_domains.py` | Lists the hosts of every evidence URL in the last N days of flags, from a directory or a git ref. With `--calls-ref` it also covers the agent's own call evidence. Each host is marked against the §6.1 allowlist. |
| `tests/test_evidence_domains.py` | 2 tests (host parsing and allowlist matching, the date window, tallying). Passing. |
| `docs/plans/prediction-agent-v2-plan.md`, `docs/plans/stats_reference.py` | The plan and its statistics companion, moved from the repo root. `python docs/plans/stats_reference.py` prints "all self-tests passed". |
| `docs/plans/phase0-discovery.md` | This report. |
| `tests/test_intel_scouts.py` (separate commit) | Fixes a time bomb found while running the suite. The orchestrator fixture's flag had a fixed date and fell out of the scouts' 24-hour pending window today, so 2 tests failed. It is now stamped with the current time. No scout code changed. All 72 intel tests pass. |

**Deviations from the plan's proposals:**

1. **`agent/` sits at the repo root**, as the plan says. The old prompt and seed playbook stay at `intel/agent/routine_prompt.md`, `playbook_v1.md` and `SETUP.md` as history. Once the thin loader is live, `intel/agent/routine_prompt.md` is dead text; delete it or mark it superseded in Phase 1.
2. **RUNBOOK step 3 keeps two lines from the live prompt** that §14.2's replacement text drops:
   - the "first run of the UTC day" test (no run record dated today), which is the mechanism the routine already uses;
   - an explicit pointer to `../data/scores/calls_scored.jsonl`, so "newly GRADED" has a concrete source.

   Otherwise steps 3 and 3b are §14.2 verbatim.
3. **The RUNBOOK report template changed with the §14.2 edits.**
   - The market-view table is now `P(up in 5 trading days)` with no "recorded as call" column.
   - The names table gains a reason-code column.
   - The heading says `FROZEN`.
4. **`evidence_domains.py` goes slightly beyond §6.1.** The plan asks for flag evidence only. I added `--calls-ref` because the agent's own evidence is where off-allowlist hosts actually appear (§3 below).

### Switching to the thin loader (owner action, not done)

§6.6 says to switch the routine's prompt to the §14.1 loader once `agent/RUNBOOK.md` is on the code branch. That happens only when this PR merges. **I have not changed the live routine.** It still runs the old prompt, with self-modification and forced macro calls. After the merge, either you change it in the web UI or you tell me to change it via the routine API.

Two §6 acceptance criteria depend on that switch: no new playbook file, and macro views appearing only in the report.

---

## 1. Repo map

### 1.1 Where things live today

| Branch | Written by | Holds |
|---|---|---|
| `feat/liquidity-core-v2` | Owner / Claude Code by hand | `intel/` package, workflows, tests. Also the unrelated `engine/` and `market_agent/` trees. |
| `main` | Owner, via plumbing commits | Workflow YAMLs only. GitHub fires scheduled triggers only from the default branch. |
| `agent-data` (orphan) | Scouts and grader Actions | `archive/flags/YYYY-MM-DD.jsonl`, `state/scout_state.json`, `ledger/runs/YYYY-MM.jsonl` (scout run records), and later `scores/` |
| `claude/agent-ledger` (orphan) | The routine | `ledger/calls/`, `ledger/runs/`, `agent/playbook_v1.md`, `agent/versions.jsonl`, `agent/lessons.jsonl`, `agent/consumed.jsonl`, `findings/YYYY/MM/DD/HHMM.md`, `LATEST.md` |

There is no `playbook` branch. Invariant 1 (§4) is **not met today**: the playbook lives on the branch the agent writes to.

### 1.2 Scouts (`intel/scouts/`, run by `.github/workflows/agent_scouts.yml`)

- **Schedule:** `*/15 3-10 * * 1-5` and `7 0-2,11-23 * * 1-5` UTC.
- **Concurrency:** group `agent-data-writer`, shared with the grader. The push to `agent-data` retries up to 5 times with a rebase.
- **Run record:** each run appends to `agent-data/ledger/runs/` (track `scout`, version `scouts_v1`). Exit code 2 means FAILED.

**Sources (14)**, as named in the run records:

| Group | Sources | Notes |
|---|---|---|
| Filings | `nse_sast_reg29`, `nse_bulk_block_deals`, `nse_announcements` | — |
| News | `rss_et_economy`, `rss_business_standard`, `rss_livemint`, `rss_moneycontrol`, `rss_investing_india`; plus `nse_equity_list` | The equity list is the company-name index used to match news. Moneycontrol returned 403 on 2 of 4 runs. |
| Regulators | `feed_rbi`, `feed_sebi`, `feed_pib` | — |
| Market | `yahoo_macro_prices` (9 instruments), `nse_bhavcopy` | The bhavcopy covers the whole market. |

**Flag schema** (`common.make_flag`):

| Field | Meaning |
|---|---|
| `flag_id` | sha1 of `kind\|key`, which is what dedupes flags |
| `kind`, `symbol`, `instrument` | What was flagged and on which name |
| `importance` | Rule-based score, 0–1 |
| `direction_hint` | Usually null |
| `summary` | One-line description |
| `evidence[]` | `{url, publisher, published_at, claim}` |
| `observed_at` | When the scout saw it (UTC) |
| `extra` | Parsed structured fields, e.g. SAST %, deal value, volume ratio |

**Kinds and importance rules:**

| Kind | Rule |
|---|---|
| `sast_acquisition` | Open-market acquisitions only. Promoter ≥0.5 pt: 0.75 + min(0.2, pct/10). Holder ending ≥5% with ≥1 pt acquired: 0.65 + … |
| `bulk_deal_buy`, `block_deal_buy` | BUY ≥ ₹5 cr: 0.30 + 0.15·log10(value/5) |
| `announcement`, `volume_spurt_notice` | Base map per announcement type |
| `news_multi_source` | ≥2 publisher groups within 6 h |
| `macro_move` | 2σ move on one of the 9 macro instruments; regulator items get 0.60 |
| `volume_breakout` | Volume ≥3× and a ≥2σ move, turnover ≥ ₹5 cr. Capped at 0.65 and tagged `reactive_by_construction`. |

**Queue and escalation** (`escalate.py`, `intel/agent_queue.py`):

- **Pending:** flags with importance ≥0.4 stay pending for 24 h.
- **Grouping and escalation:** flags are grouped per instrument. A group escalates at ≥0.70, with +0.15 for each extra independent *family* (holder / tape / disclosure / media).
- **Caps:** up to 10 groups. Fire limits are 8 a day with a 30-minute gap; text is capped at 6,000 chars.
- **No API fire in practice.** `AGENT_FIRE_URL/TOKEN` are not configured, so every run logs "queued, not fired". The routine pulls the queue itself on its hourly schedule. `--mark` writes the consumed ids.

### 1.3 Ledger, validator, grader (`intel/`)

**Ledger** (`ledger.py`):

- Layout: `calls/YYYY-MM.jsonl` and `runs/YYYY-MM.jsonl`.
- Hash-chained with `canonical_json`. `prev_hash` and `hash` start at a genesis of 64 zeros.
- CLI: `append-call | append-run | verify` with `--dir --json`. The CLI stamps `call_id`, `created_at` and the hashes.

**Call validator** (`schema.validate_call`):

- `schema == call.v1`, and all 14 required fields are present.
- The instrument resolves in `universe.py`.
- Direction is UP or DOWN; `horizon_days` is in {1, 5, 20}.
- Probability is in [0.50, 0.95] and not a bool.
- `signal_family` is one of 8: `insider_buy`, `bulk_deal`, `volume_breakout`, `news_event`, `policy_macro`, `fx_macro`, `earnings_corporate`, `other`.
- Thesis is 1–400 chars; `reaffirms` is null or a string.
- Evidence is a non-empty list. Each item has `url`, `publisher`, `published_at` and `claim` (≤200), and `published_at` ≤ `created_at` (P6 exists).

**Run validator** (`validate_run`):

- `schema == run.v1`, and all required fields are present.
- Status is one of OK, DEGRADED, FAILED, NO_NEW_INFORMATION.
- `sources` is a list; `n_calls` is an int ≥0; timestamps parse; notes ≤500.
- It does **not** check the content of `sources`. The agent wrote `ok` in one run and `OK` in others, and both passed.

**Grader** (`grader.py`, `.github/workflows/agent_grader.yml`, `41 14 * * 1-5`):

- Entry is the **open** of the first session after the call's UTC date. Exit is the close of session `entry + h − 1`.
- Benchmark is `^NSEI` for `.NS`, and none for FX, commodities and indices.
- `hit` is signed excess > 0. **No costs.**
- Statuses: PENDING, UNGRADEABLE, REAFFIRM. REAFFIRM covers explicit reaffirms and calls made while an earlier same-direction call is still open, and both are excluded from N.
- REACTIVE means the prior day's move was >2σ from the 20-day mean; those calls are reported separately.
- The momentum baseline is the sign of the 5-day pre-entry return. The permutation p uses 10,000 sign flips.
- Brier score.
- Groups are per (track, version), never pooled.
- `primary_n` counts 5-day non-reactive GRADED calls. The endpoint needs n ≥ 50, p < 0.05, and the mean above momentum.
- Outputs: `scores/calls_scored.jsonl` and `scores/summary.md` on `agent-data`.
- **It grades only calls.** Flags, NO_CALL decisions and baselines B0/B1 are not graded.
- **First scheduled run is today at 14:41 UTC**, so no `scores/` exist yet. It has not yet run against real data in Actions; its 30 unit tests use synthetic prices.

### 1.4 Evidence: archived or only linked?

**Only linked.**

- Flags keep the URL, a one-line claim, and parsed fields in `extra`. They never keep filing text.
- Calls keep the URL plus a ≤200-char claim.
- The subagents' full reports exist only in the session transcript.
- The findings markdown is a summary.

The 03:37 run shows why this matters: both SAST filings were **scanned PDFs inside zips with no text layer**, and the subagents read them as page images. Nothing is reproducible from the repo. This is the §8.2 evidence-pack gap.

### 1.5 Playbook and lessons

- **Versions:** `playbook_v1.md` only, hand-seeded 2026-10-01. `versions.jsonl` has 1 line.
- **Lessons:** `lessons.jsonl` is empty. Nothing has been graded, so the old self-modification rule has never fired. **The freeze costs nothing to apply now.**

### 1.6 Other workflows (unchanged by this work)

These are the 3 `paper_trade_*.yml` workflows for the engine/ paper bots. They run once a day, around 01:30–02:30 UTC, and fire reliably (on time to about 1h45m late). They share nothing with the agent except the repo.

---

## 2. Volumes (all of history: 15 hours)

| Metric | Value |
|---|---|
| Flags archived | **219**: 216 on 1 Oct (first run backfilled the day), 3 on 2 Oct (NSE holiday) |
| Flags by kind, 1 Oct | announcement 86, bulk_deal_buy 65, volume_breakout 28, volume_spurt_notice 25, news_multi_source 5, sast_acquisition 3, block_deal_buy 3, macro_move 1 |
| Importance ≥0.4 / ≥0.7 | 172 / 4 on 1 Oct; 3 / 0 on 2 Oct |
| Pending in scout state now | 39 |
| Routine runs | 7 sessions. 2 on 1 Oct failed with 403 on push (GitHub App not yet installed). Then 1 OK with an empty queue, then 4 today. |
| Names investigated | 0 on 1 Oct; **3 on 2 Oct** (GRASIM, AAATECH, EURUSD=X), all in the 03:37 run. The 04:37, 05:37 and 06:37 runs had an empty queue. |
| Calls | **1**: `agent-20261002T034124Z-001`, USDINR=X UP, 5d, p=0.56, `fx_macro`. **Forced by the old step 3b rule.** |
| Graded calls | 0 (grader has not run yet; the first possible grade is around 9 Oct) |
| Scout runs | **4**: 1 manual and 3 scheduled. About 29 scheduled slots passed. |

**What the volumes imply for the plan.** §16–17 assume about 40 paired opportunities a week. One day of escalations produced 3 names, and the queue rule only surfaces groups scoring ≥0.70. On a normal trading day the escalation gate passes roughly 3–6 groups. That is about 15–30 opportunities a week **if the scout schedule actually fires**, and it is far fewer while it doesn't. Grading every flag (§7.2) is what gets the volume. About 200 flags a day are available for B0 and the event studies even if the agent investigates very few.

---

## 3. §6.1 and §6.4 findings: what the routine can actually reach

**The environment is on Full network access, not Custom.** You set it during setup. So `host_not_allowed` 403s cannot happen today, and the plan's allowlist is not in force.

From the 03:37 run log:

| Reached | Blocked |
|---|---|
| yfinance; `nsearchives.nseindia.com` (SAST zips, 200); the NSE GSM API (200 JSON); `rbi.org.in`; `bls.gov`; about 15 news and finance sites | **NSE bulk-deal and block-deal APIs**: HTTP 200 but HTML instead of JSON, which is NSE's bot wall for datacenter IPs. The same endpoints work from GitHub Actions. **Site-side 403s** from fxstreet.com, bloomberg.com, business-standard.com and vtmarkets.com. These are the sites refusing the IP, not the environment. |

**`tools/evidence_domains.py` output** (`--ref ai-finance/agent-data --calls-ref ai-finance/claude/agent-ledger --days 60 --today 2026-10-02`):

```
Flags read: 219 from 2 file(s): 2026-10-01.jsonl, 2026-10-02.jsonl

| Host | URLs | Flags | Kinds | Plan allowlist |
|---|---|---|---|---|
| nsearchives.nseindia.com | 103 | 103 | announcement, sast_acquisition, volume_spurt_notice | *.nseindia.com |
| www.nseindia.com | 96 | 96 | block_deal_buy, bulk_deal_buy, volume_breakout | *.nseindia.com |
| (no url) | 13 | 13 | volume_spurt_notice | n/a |
| www.business-standard.com | 5 | 5 | news_multi_source | *.business-standard.com |
| www.livemint.com | 5 | 5 | news_multi_source | *.livemint.com |
| finance.yahoo.com | 2 | 2 | macro_move | *.yahoo.com |

Hosts not covered by the plan allowlist: none

Calls read: 1 from 1 file(s): 2026-10.jsonl

| Host | URLs | Calls | Kinds | Plan allowlist |
|---|---|---|---|---|
| hdfcsky.com | 1 | 1 | fx_macro | **NOT COVERED** |
| investinglive.com | 1 | 1 | fx_macro | **NOT COVERED** |
```

(The 13 "no url" items are second evidence entries on `volume_spurt_notice` flags. Every flag has at least one URL.)

**Reading this.** The scouts' evidence fits the plan's allowlist completely. The agent's own research does not. In the one substantive run, its subagents opened about 20 hosts beyond the list, among them:

- scanx.trade, whalesbook.com, hdfcsky.com, angelone.in, 5paisa.com;
- tradingeconomics.com, stonex.com, techtimes.com, admiralmarkets.com, energynews.pro;
- breakingdefense.com, multibagg.ai, goodreturns.in, bls.gov.

Both URLs behind the one call are off-list. **Switching to Custom with exactly the §6.1 list would cut most of the agent's current evidence.** That is a choice for you (§6 Q1), not a bug.

**Other §6.4 observations:**

- **Subagents ran on Opus.** Every subagent init line in the log shows `model=claude-opus-5-5`. The RUNBOOK's Sonnet instruction (§6.7) fixes this once it is live.
- **The run took about 4 minutes of model time for 3 names plus 6 macro views.** The session stayed open about 12 minutes because of a late notification. Empty-queue runs take about 1 minute.
- **The agent knew 2 Oct was an NSE holiday,** but the routine still fires hourly on holidays. That costs about 8 quota-light runs per holiday.

---

## 4. Gap list per phase

Effort: S ≤ half a day, M ≈ 1–2 days, L ≈ 3+ days.

### Phase 0 (remaining owner actions)

| Item | Change | Effort | Owner? |
|---|---|---|---|
| Thin loader | Replace the routine prompt with §14.1 after this PR merges | S | Owner or me via the API, on your OK |
| Setup script | Add `scipy pyarrow nselib` to the environment setup script. It currently has the old 5 packages. | S | Owner (web UI) |
| Network | Decide Full vs Custom (Q1) | S | Owner |
| Branch protection §6.3 | See conflict C5 | S | Owner; `gh` is not installed on this machine |
| Scheduler reliability | See risk R1 | S–M | Decision needed (Q2) |

### Phase 1: grader v2

| Item | File-level change | Effort |
|---|---|---|
| `append-decision` | Add `decision.v1` to `intel/schema.py` (CALL/NO_CALL, `no_call_reason`, role, model, `playbook_version`, `process_version`, `rules_applied`, `evidence_pack_id`, `opportunity_id`) and a CLI verb in `intel/ledger.py`. Keep `append-call` readable; the grader maps old calls to decisions. | M |
| Opportunity id | Flags already have a stable `flag_id`. An escalated *group* has no id; define `opportunity_id` = instrument + sorted flag_ids hash, written by `agent_queue`. | S |
| Grade every flag | New `intel/grader_v2.py` reading `archive/flags/*` plus decisions. It needs a per-kind **default direction** for B0; see C4. | M |
| Entry, benchmark, prices | Entry rule (C1) and benchmark mapping (C2) in config. Bhavcopy-adjusted prices need a corporate-actions adjuster, which does not exist. Today only the current day's bhavcopy is fetched; history comes from Phase 3. | L |
| Costs | `config/costs.yaml` plus a port of `stats_reference.CostConfig` into `intel/stats/`. Liquidity bucket from 20-day median turnover, which needs bhavcopy history. | M |
| F&O list for `tradable` | New fetch of NSE's F&O underlying list (scout or grader). | S |
| Stats | Port `stats_reference.py` to `intel/stats/` with its self-tests as pytest. Add `week_block_bootstrap_ci`, Wilson, isotonic, `RobustEProcess` and `is_visible_week`. | M |
| Outputs | `scores/opportunities_scored.jsonl`, `decisions_scored.jsonl`, `stats/visible/*`, `stats/summary.md`, `monitors/harm.json`. Run in shadow next to the v1 grader in the same workflow. | M |
| Forced macro call | Exclude or correct `agent-20261002T034124Z-001` (see C6). | S |
| Macro views | Not in the ledger today (only in findings markdown). Needs `append-view` plus `agent/macro_views.jsonl` before Brier vs climatology is possible. The plan places this in Phase 2 (§8.5), but the RUNBOOK already asks for views, so the data is being lost until then. | S |

### Phase 2: fast loop

| Item | Change | Effort |
|---|---|---|
| P2 surveillance | Fetch the ASM/GSM lists. The NSE GSM API works from the routine; ASM is unverified. Put the fetch in scouts and attach the lists to flags. | S |
| P3 liquidity | 20-day median turnover per symbol. Bhavcopy gives one day; it needs a rolling store on `agent-data` (about 2,000 rows a day). | M |
| P1, P4, P7, P9 | Validator plus grader checks. P7 (results inside the horizon) needs NSE's board-meeting calendar, a new fetch. P9 needs HEAD requests from Actions; many sites 403 datacenter IPs (see §3), so P9 will over-flag. | M |
| P8 sensor | Compare evidence-pack numbers with scout `extra`. **The SAST sensor has a known semantic gap:** Reg 29 percentages are cumulative over the disclosure period (Grasim's "2.01%" spanned 31 months). The scout must parse the acquisition period from the filing, which is a scanned PDF. | M–L |
| Evidence packs | New `agent/evidence/<id>.json` on the ledger branch, plus `intel/price_context.py` (deterministic, replaces the chart subagent). | M |
| Scouts attach filing text | Bulk/block JSON works from Actions but not the routine, so attach it there. SAST filings are scanned PDFs, so "attach text" means OCR or attaching the parsed XBRL where NSE provides it (unverified). | M–L |
| Questions | `agent/questions.jsonl` plus a deterministic resolver for RBI and results. | M |

### Phase 3: history

| Item | Change | Effort |
|---|---|---|
| Ingestion | `nselib` is not installed or tested anywhere yet. Cache raw files outside the code branch (Q3). | L |
| Insider/SAST history | The NSE PIT feed is **stale**: newest record 2026-05-02 when probed on 1 Oct. Whether the SAST Reg 29 archive goes back to 2015 through the API is unverified. This is the biggest unknown for Phase 3. | M (probe) |
| Event study, placebo, base-rate cards, det-rule backtester | All new, under `intel/history/`. | L |

### Phase 4: slow loop

| Item | Change | Effort |
|---|---|---|
| `playbook` branch | Orphan branch plus `rules.yaml` migrated from `playbook_v1.md` by PR. Protection needs C5 settled first. | M |
| Gate Action, e-LOND, auto-merge PR | New workflow. Auto-merge needs a token with PR rights, and repo settings to allow auto-merge. | L |
| Retire old mechanism | Remove `agent/playbook_v*.md` reading from the RUNBOOK; the ledger-branch files stay as history. | S |

Phase 5 (dashboard) and Phase 6 (`GO_LIVE.md`) have no blockers beyond the phases before them.

---

## 5. Where the plan conflicts with the code

| # | Plan | Code today | Suggested resolution |
|---|---|---|---|
| C1 | **Entry** = close of the decision day if before 15:00 IST, else the next day's close (§7.2) | Next session's **open** after the call's UTC date | Adopt the plan's rule in grader v2; keep v1 as-is for continuity. Note that a 10:00 IST decision entering at that day's close assumes the agent could act intraday, and the routine can. |
| C2 | Sector or size benchmark | `^NSEI` only, hardcoded in `universe.benchmark_for` | Move it to config in Phase 1; this needs a symbol→sector→index map (NSE index constituents files). |
| C3 | All payoffs net of costs | No costs | Phase 1. |
| C4 | B0 takes each flag in "its signal family's default direction" | Scouts emit **`kind`** (8 values, e.g. `announcement`, `volume_spurt_notice`), not the call schema's `signal_family`, and `direction_hint` is mostly null | A `kind → (signal_family, default_direction)` table in config. Some kinds have no honest default: an `announcement` can be anything, and `volume_breakout` follows the day's move. Proposal: those kinds are graded but excluded from B0, and the table is reviewed by you. |
| C5 | §6.3: protect `agent-data` and require PRs | The scouts and the grader push **directly** to `agent-data` every run | A "require PR" rule on `agent-data` breaks both. Use a ruleset that blocks force-push and deletion, plus a push restriction to GitHub Actions (bypass list), not a PR requirement. `main` and the code branch can require PRs. |
| C6 | No forced macro calls; macro views never in the ledger | One forced macro call is already in the append-only ledger | Invariant 2 forbids editing it. Grader v2 should exclude `fx_macro` and `policy_macro` calls made by the step-3b rule, identified by the call id list `[agent-20261002T034124Z-001]` in config. Alternatively, accept one contaminated row. |
| C7 | Invariant 1: the agent never edits the playbook branch | The playbook is on `claude/agent-ledger`, which the agent writes | Resolved by Phase 4's `playbook` branch. Until then, the RUNBOOK freeze is a prompt rule only. |
| C8 | §5: routine runs hourly 09:07–16:07 IST | Cron `37 3-10 * * 1-5` = 09:07–16:07 IST, as planned | No conflict. Noted because the plan's diagram matches. |
| C9 | Subagent model Sonnet | Opus everywhere today | Fixed by the RUNBOOK once live. |

---

## 6. Risks, unknowns, and questions for you

### Risks

- **R1. The scout schedule barely fires.** 3 of about 29 scheduled slots ran in the last 15 hours. The once-a-day paper-bot crons fire, so GitHub is dropping the dense `*/15` schedule, which is known behaviour for new or low-activity workflows under load. This caps the volume everything in §16–17 depends on. Options:
  - (a) the routine runs `python -m intel.scouts.run_scouts` itself at the start of each hourly run. This is reliable, but NSE deal APIs are blocked from the routine (§3), so filings would degrade.
  - (b) an external cron (e.g. cron-job.org) calls `workflow_dispatch`; this needs a fine-grained PAT stored outside GitHub.
  - (c) fewer, odd-minute slots, e.g. `11,41 3-10`, and measure again.

  I'd do (c) now and (b) if it is still under 80% after a week.
- **R2. Evidence reproducibility.** Nothing the agent read is stored (§1.4). Until evidence packs exist, no decision can be replayed or reviewed outcome-blind.
- **R3. Volume.** At the current escalation gate, the agent sees about 3 names a day. The plan's realistic-learning timeline (§17) assumes far more. Grading all flags and B0 is the cheap way to get there.
- **R4. Holiday runs.** The routine and scouts run on NSE holidays. They need an NSE holiday calendar check; cheap to add.
- **R5. Site blocking from cloud IPs.** NSE deal APIs and several news sites refuse the routine. More of the evidence load should shift to Actions-side scouts (§8.2), which is also where Akamai lets us in.

### Unknowns (unverified)

- Whether NSE's SAST/PIT archive APIs serve 2015-onward history. This is the gate for Phase 3's insider family.
- Whether `nselib` 2.5.1 works from Actions IPs.
- How the cloud environment's matcher treats `*.example.com` versus the bare `example.com`. The tool marks such matches "bare domain, unverified".
- The grader's first real run happens today at 14:41 UTC. It has only been tested on synthetic data.

### Questions

1. **Network:** keep **Full** access (today), switch to **Custom** with the §6.1 list (cuts most agent evidence; see §3), or Custom with a longer list? My view: stay on Full until evidence packs exist and show which hosts matter, then narrow.
2. **Scheduler:** OK to try odd-minute slots (R1 option c) as a tiny ops change before Phase 1?
3. **Branch protection:** OK with C5's resolution (Actions-only push restriction on `agent-data`, no PR requirement)? `gh` isn't installed here, so you'd apply it in the GitHub UI, or install `gh` and I'll do it.
4. **The forced USDINR call (C6):** exclude it by id in grader v2, or let it stand as a known-contaminated row?
5. **B0 defaults (C4):** OK to exclude kinds without an honest default direction (`announcement`, `volume_breakout`, `news_multi_source`) from B0 rather than inventing one?
6. **Thin loader:** after merging, do you switch the prompt in the web UI, or shall I do it through the routine API?
7. Still open from earlier: your own patterns and trusted sources for the playbook. Under the v2 plan these become `judg` rules or `det` predicates to test, not prose.

The plan's own §18 questions (benchmark, usage tier, history storage, DOWN calls, harm-alert channel, checklist edits, go-live criteria) still stand and are not repeated here.

---

## 7. Phase 0 acceptance status

| Criterion | Status |
|---|---|
| The next scheduled run reaches yfinance and at least one NSE or news source; sources OK | **Met by the 03:37 run** (yfinance OK, NSE SAST OK, news OK; NSE bulk/block blocked). The later runs had empty queues and touched no sources, so "next run after Phase 0" is not yet demonstrated. |
| No new playbook file is created | **Met so far** (still only v1). It is enforced only once the RUNBOOK is live; the old prompt still permits new versions, though they can't trigger without graded misses. |
| Macro views appear only in the report | **Not met.** One forced macro call exists, and the old prompt is still live. Met once the thin loader is switched on. |
| Discovery report committed | **Met** by this PR. |

**Stopping here.** Next step is your review of this report and the RUNBOOK, then your answers to §6. No Phase 1 work has started.
