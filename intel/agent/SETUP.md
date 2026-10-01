# Prediction agent — one-time setup (done by the user in claude.ai / GitHub)

The scouts and the grader already run on their own (GitHub Actions). Until the steps below are
done, the scouts queue escalations and say "AGENT_FIRE_URL/TOKEN not configured" in every run
record — nothing is lost, the agent simply isn't woken yet. API-trigger tokens can only be
created in the claude.ai web UI, which is why this part is manual.

## 1. Cloud environment (network access)
claude.ai/code/routines → New routine → environment selector → create environment `market-agent`:
- Network access: **Custom**, tick "Also include default list", and allow:
  `nseindia.com, nsearchives.nseindia.com, bseindia.com, sebi.gov.in, rbi.org.in, pib.gov.in,
  economictimes.indiatimes.com, business-standard.com, livemint.com, moneycontrol.com,
  investing.com, in.investing.com, fxstreet.com, finance.yahoo.com, query1.finance.yahoo.com,
  query2.finance.yahoo.com, fc.yahoo.com`
  (If runs keep reporting blocked hosts, switch to **Full**.)
- Setup script: `pip install pandas numpy requests feedparser yfinance`

## 2. The routine
- Name: `Prediction agent`
- Model: **Opus**
- Repository: `magic-emperor/AI-finance`
- Environment: `market-agent`
- Instructions: paste the whole of `intel/agent/routine_prompt.md`
- Connectors: **remove all of them** (they aren't needed, and routines can use connector write
  tools without asking)
- Triggers:
  - **Schedule**: weekdays at 07:37 (IST) — the daily reflection heartbeat
  - **API**: add after saving → copy the URL → **Generate token** (shown once — copy it)

## 3. Give the scouts the trigger
GitHub → `magic-emperor/AI-finance` → Settings → Secrets and variables → Actions → New secret:
- `AGENT_FIRE_URL` = the routine's `/fire` URL
- `AGENT_FIRE_TOKEN` = the token

## 4. Dry-run before trusting it
On the routine page click **Run now** (no text) once — that is a heartbeat run. Open the session
and read it: it should set up the worktrees, find no graded calls, and commit one
NO_NEW_INFORMATION run record to `claude/agent-ledger`. A green status alone proves nothing
(per the routines docs); the committed run record does.

## Costs and limits (verified 2026-10-01)
- Scouts and grader: GitHub Actions on a public repo — free.
- The agent: runs draw on your claude.ai subscription quota (same pool as interactive use).
  The scouts cap escalations at 8/day with a 30-minute minimum gap, plus 1 heartbeat/weekday.
- Routines are a research preview; the `/fire` endpoint sits behind a dated beta header and
  may change.
