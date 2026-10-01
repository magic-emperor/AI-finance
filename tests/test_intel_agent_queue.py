"""The agent's own queue reader: same escalation gate as the scouts, and never re-serves a flag
the agent already investigated."""
from __future__ import annotations

import json

from intel import agent_queue
from intel.scouts.common import make_flag


def flag(kind, sym, imp, fid):
    return make_flag(kind, fid, sym, imp, f"{kind} {sym}",
                     [{"url": "https://u", "publisher": "NSE", "published_at": "2026-10-01T05:00:00Z", "claim": "c"}],
                     "2026-10-01T05:00:00Z")


def write_state(tmp_path, pending, escalated=None):
    d = tmp_path / "data" / "state"
    d.mkdir(parents=True)
    (d / "scout_state.json").write_text(json.dumps({"pending": pending, "escalated": escalated or {}}))
    return str(tmp_path / "data")


def test_queue_applies_the_scouts_gate_and_skips_consumed(tmp_path, capsys):
    big = flag("sast_acquisition", "GRASIM", 0.95, "g")
    small = flag("announcement", "XYZ", 0.55, "x")
    data = write_state(tmp_path, [big, small])
    consumed = str(tmp_path / "ledger" / "agent" / "consumed.jsonl")
    assert [g["symbol"] for g in agent_queue.pending_groups(data, consumed)] == ["GRASIM"]

    agent_queue.main(["--data", data, "--consumed", consumed])
    out = capsys.readouterr().out
    assert "GRASIM" in out and big["flag_id"] in out

    agent_queue.main(["--data", data, "--consumed", consumed, "--mark", big["flag_id"]])
    assert agent_queue.pending_groups(data, consumed) == []
    agent_queue.main(["--data", data, "--consumed", consumed])
    assert "QUEUE EMPTY" in capsys.readouterr().out


def test_missing_state_means_empty_queue(tmp_path):
    assert agent_queue.pending_groups(str(tmp_path / "nope"), str(tmp_path / "c.jsonl")) == []
