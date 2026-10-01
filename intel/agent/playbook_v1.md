# Prediction agent playbook — v1

This is the agent's own methodology. It is the ONLY document the agent may rewrite (as a new
version file, never in place). Seeded by hand on 2026-10-01; every later version is written by
the agent itself under the self-improvement rules in the routine prompt.

## What a call is

A call is a falsifiable bet: one instrument, UP or DOWN versus its benchmark (Nifty 50 for NSE
stocks; none for FX/commodities/indices), over 1, 5 or 20 trading days, with a probability.
Entry is the next session's open after the call; nothing earlier counts. Zero calls is a normal,
respectable outcome. A call nobody can check is worth nothing.

## Default stance

- **Most flags are not trades.** The scouts flag attention, not opportunity. Start from "no call"
  and require the evidence to move you off it.
- **Already moved = probably priced in.** If the move happened before you saw the flag (volume
  breakout, >2-sigma day), the information is likely in the price. Calls right after such moves
  are graded separately as REACTIVE; they rarely earn anything. Prefer information the price has
  NOT yet absorbed: a filing published after the close, a disclosure the press hasn't picked up,
  a policy release minutes old.
- **Calibrate.** Real edges are small. Most honest probabilities sit in 0.52–0.62. Anything above
  0.70 needs an exceptional, specific reason, stated in the thesis.

## Signals, in rough order of prior credibility

1. **Open-market buying by promoters / 5%+ holders (SAST Reg 29).** The user's core observation
   ("when an important person invests, the stock tends to rise"), on real disclosed data. Insider
   *buys* are informative in the literature; sales are mostly liquidity-driven, so do not short on
   a sale alone. Weigh size relative to holding, buyer identity, and whether the price already ran.
2. **Company disclosures with real content**: order wins sized against revenue, acquisitions,
   regulatory actions against the company. Check size vs market cap; small orders at large
   companies are noise.
3. **Policy / regulator releases (RBI, SEBI, PIB)**: map to the specific instruments they move
   (INR crosses, bank stocks, a named sector). Vague circulars are not calls.
4. **Multi-publisher news** on a named company: corroboration that the story is real, NOT a
   direction. Read what the story says.
5. **Bulk/block deals and volume spikes**: tape activity. Mostly brokers, prop desks, operators.
   Only meaningful when an independent signal above explains it.

## Red flags — do not call, or call DOWN only with strong independent reason

- Average daily turnover under Rs 5 crore (untradeable; spikes are often manufactured).
- The stock is on NSE's ASM/GSM surveillance lists.
- The only evidence is a social-media or single-outlet story.
- The thesis needs a fact you could not open and read this run.

## User-supplied patterns and trusted sources

_Pending from the user (2026-10-01). When provided, they are added here as a new version (v2) and
recorded as such — not edited into v1._

## How the agent reviews itself

See the routine prompt: reflections on graded calls go to `agent/lessons.jsonl`; a method change is
applied only when it cites at least 3 graded call_ids showing the same flaw, at most one new
version per day, and a version that underperforms its predecessor after 15+ graded calls is
reverted.
