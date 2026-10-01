"""
Tests for intel.ledger / intel.validate / intel.schema.

Guards the properties that make a prediction ledger worth grading:
  - the agent cannot backdate (timestamps and hashes are stamped, not supplied)
  - any edit to a past line is detected
  - malformed or ungradeable predictions are rejected, not stored
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from intel import ledger
from intel.validate import validate_ledger

NOW = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_call(**over):
    base = {
        "track": "agent", "version": "v1", "run_id": "run-1",
        "instrument": "RELIANCE.NS", "direction": "UP", "horizon_days": 5,
        "probability": 0.58, "signal_family": "insider_buy",
        "thesis": "Promoter bought Rs 5cr on market.",
        "evidence": [{"url": "https://www.nseindia.com/x", "publisher": "NSE",
                      "published_at": "2026-10-01T07:00:00Z", "claim": "Promoter market buy"}],
    }
    base.update(over)
    return base


def make_run(**over):
    base = {"run_id": "run-1", "track": "agent", "version": "v1",
            "started_at": "2026-10-01T09:55:00Z", "status": "OK",
            "sources": [{"source": "nse_pit", "status": "OK", "n_items": 3}], "n_calls": 1}
    base.update(over)
    return base


def test_append_stamps_timestamp_and_hash_and_chain_is_intact(tmp_path):
    d = str(tmp_path)
    r1 = ledger.append_call(d, make_call(), now=NOW)
    r2 = ledger.append_call(d, make_call(instrument="TCS.NS"), now=NOW)
    assert r1["created_at"] == "2026-10-01T10:00:00Z"
    assert r1["prev_hash"] == "0" * 64
    assert r2["prev_hash"] == r1["hash"]
    assert r1["call_id"] != r2["call_id"]
    assert validate_ledger(d) == []


def test_agent_supplied_timestamp_is_overwritten_not_trusted(tmp_path):
    rec = ledger.append_call(str(tmp_path), make_call(created_at="2020-01-01T00:00:00Z",
                                                      hash="forged"), now=NOW)
    assert rec["created_at"] == "2026-10-01T10:00:00Z"
    assert rec["hash"] != "forged"


def test_editing_a_past_line_breaks_the_chain(tmp_path):
    d = str(tmp_path)
    ledger.append_call(d, make_call(), now=NOW)
    ledger.append_call(d, make_call(instrument="TCS.NS"), now=NOW)
    path = tmp_path / "calls" / "2026-10.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["direction"] = "DOWN"
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("edited" in e for e in ledger.verify_chain(d, "calls"))
    assert validate_ledger(d) != []


def test_deleting_a_line_breaks_the_chain(tmp_path):
    d = str(tmp_path)
    for sym in ("A.NS", "B.NS", "C.NS"):
        ledger.append_call(d, make_call(instrument=sym), now=NOW)
    path = tmp_path / "calls" / "2026-10.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ledger.verify_chain(d, "calls") != []


@pytest.mark.parametrize("override,fragment", [
    ({"instrument": "NOTREAL"}, "universe"),
    ({"direction": "SIDEWAYS"}, "direction"),
    ({"horizon_days": 3}, "horizon"),
    ({"probability": 0.99}, "probability"),
    ({"probability": 0.40}, "probability"),
    ({"signal_family": "vibes"}, "signal_family"),
    ({"thesis": ""}, "thesis"),
    ({"thesis": "x" * 401}, "thesis"),
    ({"evidence": []}, "evidence"),
])
def test_invalid_calls_are_rejected_and_not_written(tmp_path, override, fragment):
    with pytest.raises(ValueError, match=fragment):
        ledger.append_call(str(tmp_path), make_call(**override), now=NOW)
    assert ledger.read_records(str(tmp_path), "calls") == []


def test_evidence_published_after_the_call_is_rejected(tmp_path):
    bad = make_call(evidence=[{"url": "https://x", "publisher": "NSE",
                               "published_at": "2026-10-01T11:00:00Z", "claim": "from the future"}])
    with pytest.raises(ValueError, match="after the call"):
        ledger.append_call(str(tmp_path), bad, now=NOW)


def test_run_record_roundtrip_and_status_enum(tmp_path):
    d = str(tmp_path)
    ledger.append_run(d, make_run(), now=NOW)
    assert validate_ledger(d) == []
    with pytest.raises(ValueError, match="status"):
        ledger.append_run(d, make_run(status="GREAT"), now=NOW)


def test_cli_rejects_bad_call_with_nonzero_exit(tmp_path, capsys):
    code = ledger.main(["append-call", "--dir", str(tmp_path),
                        "--json", json.dumps(make_call(probability=2))])
    assert code == 1
    assert "REJECTED" in capsys.readouterr().err
