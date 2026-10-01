# claude/agent-ledger

Written only by the prediction-agent routine.

- `ledger/calls/` — every prediction, hash-chained; any edit to a past line is detectable
- `ledger/runs/` — one record per agent run, including runs with no calls
- `agent/playbook_vN.md` — the agent's methodology; it may add new versions, never edit old ones
- `agent/lessons.jsonl` — its reflection on each graded wrong call
- `agent/versions.jsonl` — why each new version was created, with the call_ids that justified it
- `findings/` — free-form reasoning (never graded)
