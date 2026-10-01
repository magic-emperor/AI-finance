# agent-data

Written only by CI: the `Agent scouts` and `Agent grader` workflows. Never edit by hand.

- `state/scout_state.json` — dedup, pending flags, bhavcopy history, fire log
- `archive/flags/YYYY-MM-DD.jsonl` — every flag the scouts ever raised (provenance)
- `ledger/runs/` — hash-chained record of every scout run, including quiet and failed ones
- `scores/` — the grader's verdicts on the agent's calls (`summary.md`, `calls_scored.jsonl`)
