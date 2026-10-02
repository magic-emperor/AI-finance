"""
The decision record (plan v2 §7.1): every investigated opportunity gets CALL or NO_CALL with a
reason; the ledger stamps ids, time and hashes; the queue hands out the opportunity ids.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from intel import agent_queue
from intel.ledger import append_decision, main as ledger_main, read_records, verify_chain
from intel.scouts import escalate
from intel.validate import validate_ledger

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
EV = [{"url": "https://nsearchives.nseindia.com/x.zip", "publisher": "NSE",
       "published_at": "2026-10-05T03:00:00Z", "claim": "Reg29: promoter bought 1.2% on 01-Oct"}]


def base(**o):
    d = {"track": "agent", "role": "champion", "model": "claude-opus-5-5", "playbook_version": "v1",
         "process_version": "p0", "run_id": "r1", "opportunity_id": "opp:GRASIM:0123456789ab",
         "flag_ids": ["d67a4e7e61eef399"], "instrument": "GRASIM.NS", "thesis": "why"}
    d.update(o)
    return d


CALL = base(decision="UP", horizon_days=5, probability=0.58, signal_family="insider_buy", evidence=EV)
NO_CALL = base(decision="NO_CALL", no_call_reason="stale", evidence=EV)


def test_call_and_no_call_are_stamped_and_chained(tmp_path):
    a = append_decision(str(tmp_path), CALL, NOW)
    b = append_decision(str(tmp_path), NO_CALL, NOW)
    assert (a["decision_id"], b["decision_id"]) == ("dec-20261005T040000Z-001", "dec-20261005T040000Z-002")
    assert b["horizon_days"] is None and b["probability"] is None and b["rules_applied"] == []
    assert verify_chain(str(tmp_path), "decisions") == [] and validate_ledger(str(tmp_path)) == []


def test_caller_cannot_supply_its_own_id_or_timestamp(tmp_path):
    rec = append_decision(str(tmp_path), dict(CALL, decision_id="mine", created_at="2020-01-01T00:00:00Z"), NOW)
    assert rec["decision_id"].startswith("dec-20261005") and rec["created_at"] == "2026-10-05T04:00:00Z"


def test_no_call_without_evidence_is_fine(tmp_path):
    append_decision(str(tmp_path), base(decision="NO_CALL", no_call_reason="low_liquidity"), NOW)


@pytest.mark.parametrize("bad,why", [
    (base(decision="NO_CALL"), "no_call_reason"),
    (base(decision="NO_CALL", no_call_reason="boring"), "no_call_reason"),
    (base(decision="NO_CALL", no_call_reason="stale", probability=0.6), "leave probability null"),
    (dict(CALL, probability=0.97), "probability"),
    (dict(CALL, no_call_reason="stale"), "no_call_reason null"),
    (dict(CALL, evidence=[]), "evidence"),
    (dict(CALL, evidence=[dict(EV[0], published_at="2026-10-05T05:00:00Z")]), "published after"),
    (dict(CALL, opportunity_id="GRASIM"), "opportunity_id"),
    (dict(CALL, flag_ids=[]), "flag_ids"),
    (dict(CALL, role="boss"), "role"),
    (dict(CALL, instrument="GRASIM"), "instrument"),
    (dict(CALL, decision="SIDEWAYS"), "decision must be"),
])
def test_invalid_decisions_are_rejected_and_nothing_is_written(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        append_decision(str(tmp_path), bad, NOW)
    assert read_records(str(tmp_path), "decisions") == []


def test_challenger_role_is_accepted(tmp_path):
    append_decision(str(tmp_path), dict(CALL, role="challenger:P-0003"), NOW)


def test_cli_append_decision_and_verify(tmp_path, capsys):
    # the CLI stamps the real clock, so evidence must be dated in the past
    past = [dict(EV[0], published_at="2026-01-01T00:00:00Z")]
    assert ledger_main(["append-decision", "--dir", str(tmp_path), "--json", json.dumps(dict(NO_CALL, evidence=past))]) == 0
    assert json.loads(capsys.readouterr().out)["id"].startswith("dec-")
    assert ledger_main(["append-decision", "--dir", str(tmp_path), "--json", json.dumps(base(decision="UP"))]) == 1
    assert ledger_main(["verify", "--dir", str(tmp_path)]) == 0


def _flag(fid, sym, kind="sast_acquisition", imp=0.9):
    return {"flag_id": fid, "kind": kind, "symbol": sym, "instrument": f"{sym}.NS", "importance": imp,
            "direction_hint": "UP", "summary": "s", "observed_at": "2026-10-05T03:00:00Z",
            "evidence": [{"url": "https://x", "published_at": "2026-10-05T03:00:00Z"}], "extra": {}}


def test_opportunity_id_is_deterministic_and_valid_for_the_schema():
    a = escalate.opportunity_id([_flag("b", "AAA"), _flag("a", "AAA")])
    assert a == escalate.opportunity_id([_flag("a", "AAA"), _flag("b", "AAA")])
    assert a != escalate.opportunity_id([_flag("a", "AAA")])
    assert a.startswith("opp:AAA:") and len(a.split(":")[2]) == 12
    from intel.schema import _OPP_RE
    assert _OPP_RE.match(a) and _OPP_RE.match(escalate.opportunity_id([_flag("z", "M&M")]))


def test_queue_prints_each_opportunity_with_its_flags(tmp_path, capsys):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "scout_state.json").write_text(json.dumps(
        {"pending": [_flag("f1", "AAA"), _flag("f2", "AAA", "news_multi_source", 0.5)], "escalated": {}}))
    agent_queue.main(["--data", str(tmp_path), "--consumed", str(tmp_path / "c.jsonl")])
    out = capsys.readouterr().out
    opp = escalate.opportunity_id([_flag("f1", "AAA"), _flag("f2", "AAA")])
    assert f"{opp} AAA -> f1 f2" in out and f"| {opp}" in out
