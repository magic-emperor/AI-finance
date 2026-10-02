"""
Tests for the NSE calendar, holiday-aware pending expiry, SAST period weighting and the macro-view
ledger. Fixtures only, no network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from intel import nse_calendar, views
from intel.scouts import escalate, filings, run_scouts

UTC = timezone.utc
PAYLOAD = {"CM": [
    {"tradingDate": "26-Jan-2026", "description": "Republic Day"},
    {"tradingDate": "02-Oct-2026", "description": "Mahatma Gandhi Jayanti"},
    {"tradingDate": "20-Oct-2026", "description": "Dussehra"},
], "FO": [{"tradingDate": "01-Jan-2026", "description": "not cash market"}]}
CAL = {"fetched_at": "2026-10-01T00:00:00Z", "holidays": nse_calendar.parse_holidays(PAYLOAD)}


# ── calendar ──────────────────────────────────────────────────────────────────
def test_parse_keeps_cash_market_rows_only_and_rejects_empty():
    assert [h["date"] for h in CAL["holidays"]] == ["2026-01-26", "2026-10-02", "2026-10-20"]
    with pytest.raises(ValueError):
        nse_calendar.parse_holidays({"CM": []})


@pytest.mark.parametrize("day,verdict", [
    (date(2026, 10, 2), "HOLIDAY"),          # Friday, Gandhi Jayanti
    (date(2026, 10, 3), "CLOSED"),           # Saturday
    (date(2026, 10, 5), "TRADING_DAY"),
    (date(2027, 1, 4), "UNKNOWN"),           # calendar doesn't cover 2027
])
def test_status_for(day, verdict):
    assert nse_calendar.status_for(CAL, day)[0] == verdict


def test_missing_calendar_is_unknown_and_counts_as_trading():
    assert nse_calendar.status_for(None, date(2026, 10, 2))[0] == "UNKNOWN"
    assert nse_calendar.is_trading_day(None, date(2026, 10, 2))


def test_next_session_close_skips_holidays_and_weekends():
    thu_evening = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    assert nse_calendar.next_session_close(CAL, thu_evening) == datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    wed_morning = datetime(2026, 9, 30, 5, 0, tzinfo=UTC)
    assert nse_calendar.next_session_close(CAL, wed_morning) == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


class _Session:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.calls = payload, fail, 0

    def get_json(self, url):
        self.calls += 1
        if self.fail:
            raise RuntimeError("HTTP 403")
        return self.payload


def test_refresh_fetches_once_a_day_and_a_failure_keeps_the_old_file(tmp_path):
    now = datetime(2026, 10, 2, 4, 0, tzinfo=UTC)
    s = _Session(PAYLOAD)
    assert nse_calendar.refresh(str(tmp_path), s, now)["status"] == "OK"
    assert nse_calendar.refresh(str(tmp_path), s, now + timedelta(hours=3)) is None and s.calls == 1
    bad = nse_calendar.refresh(str(tmp_path), _Session(fail=True), now + timedelta(days=2))
    assert bad["status"] == "FAILED" and "403" in bad["error"]
    assert nse_calendar.load(str(tmp_path))["holidays"] == CAL["holidays"]


def test_cli_prints_the_verdict(tmp_path, capsys):
    nse_calendar.refresh(str(tmp_path), _Session(PAYLOAD), datetime(2026, 10, 2, tzinfo=UTC))
    nse_calendar.main(["--data", str(tmp_path), "--date", "2026-10-02"])
    assert capsys.readouterr().out.strip() == "HOLIDAY: Mahatma Gandhi Jayanti"


# ── pending flags survive to the next trading session ────────────────────────
def _pending_after(now, cal):
    state = {"seen": {}, "escalated": {}, "fires": [],
             "pending": [{"flag_id": "f", "observed_at": "2026-10-01T14:00:00Z"}]}   # Thu, after close
    run_scouts.prune(state, now, cal)
    return state["pending"]


def test_flag_raised_before_a_holiday_weekend_waits_for_monday():
    sunday = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)          # 70h later: the old 24h rule dropped it
    assert _pending_after(sunday, CAL)
    assert _pending_after(datetime(2026, 10, 5, 9, 0, tzinfo=UTC), CAL)    # Monday, before close
    assert not _pending_after(datetime(2026, 10, 5, 10, 1, tzinfo=UTC), CAL)


def test_pending_never_shorter_than_24h_and_weekends_known_without_a_calendar():
    seen = "2026-09-30T09:59:00Z"                              # one minute before Wednesday's close
    assert run_scouts.pending_expiry(seen, CAL) == datetime(2026, 10, 1, 9, 59, tzinfo=UTC)
    fri_evening = "2026-10-09T14:00:00Z"
    assert run_scouts.pending_expiry(fri_evening, None) == datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


# ── SAST acquisition period ───────────────────────────────────────────────────
def _sast(**o):
    r = {"acqSaleType": "Acquisition", "acquirerName": "Mr. X", "company": "Co Ltd", "symbol": "CO",
         "promoterType": "Y", "totAcqShare": "2.01", "totAftShare": "40.47",
         "acquisitionMode": "Open Market", "application_no": "1", "timestamp": "01-Oct-2026 11:14",
         "acquirerDate": "29-SEP-2026 to 29-SEP-2026", "attachement": "https://nsearchives.nseindia.com/x.zip"}
    r.update(o)
    return r


@pytest.mark.parametrize("text,expected", [
    ("07-FEB-2024 to 24-SEP-2026", (date(2024, 2, 7), date(2026, 9, 24))),
    ("16-SEP-2026", (date(2026, 9, 16), date(2026, 9, 16))),
    ("24-SEP-2026 to 07-FEB-2024", None),           # reversed
    ("", None), (None, None), ("sometime", None),
])
def test_parse_sast_period(text, expected):
    assert filings.parse_sast_period(text) == expected


def test_multi_year_filing_is_not_scored_as_a_fresh_buy():
    """The real GRASIM filing of 2026-10-01: 2.01% bought over 960 days."""
    (fresh,) = filings.sast_flags([_sast()])
    (old,) = filings.sast_flags([_sast(acquirerDate="07-FEB-2024 to 24-SEP-2026")])
    assert fresh["importance"] == pytest.approx(0.95, abs=1e-3) and fresh["importance"] >= escalate.ESCALATE_AT
    assert old["importance"] == pytest.approx(0.95 * 0.55, abs=1e-3)
    assert old["importance"] < escalate.ESCALATE_AT
    assert old["extra"]["span_days"] == 960 and old["extra"]["days_end_to_filing"] == 7
    assert "960 days" in old["summary"] and "960 days" in old["evidence"][0]["claim"]


def test_unknown_period_is_down_weighted_and_said_so():
    (f,) = filings.sast_flags([_sast(acquirerDate=None)])
    assert f["importance"] == pytest.approx(0.95 * 0.80, abs=1e-3)
    assert f["extra"]["span_days"] is None and "unstated period" in f["summary"]


def test_span_factor_bands():
    assert [filings.sast_span_factor(d) for d in (0, 7, 8, 30, 31, 90, 91, None)] == \
        [1.0, 1.0, 0.85, 0.85, 0.70, 0.70, 0.55, 0.80]


# ── macro views ───────────────────────────────────────────────────────────────
def _sub(**o):
    s = {"run_id": "r1", "version": "v1",
         "views": [{"instrument": i, "p_up": 0.5, "reason": "no view"} for i in views.VIEW_INSTRUMENTS]}
    s.update(o)
    return s


def test_append_stamps_six_chained_lines(tmp_path):
    path = str(tmp_path / "agent" / "macro_views.jsonl")
    now = datetime(2026, 10, 5, 3, 40, tzinfo=UTC)
    recs = views.append_views(path, _sub(), now)
    assert [r["instrument"] for r in recs] == list(views.VIEW_INSTRUMENTS)
    assert {r["view_set_id"] for r in recs} == {"view-20261005T034000Z"}
    assert all(r["created_at"] == "2026-10-05T03:40:00Z" and r["horizon_days"] == 5 for r in recs)
    views.append_views(path, _sub(run_id="r2"), now + timedelta(days=1))
    assert views.verify(path) == [] and len(views.read_views(path)) == 12


def test_edited_view_breaks_the_chain(tmp_path):
    path = tmp_path / "v.jsonl"
    views.append_views(str(path), _sub(), datetime(2026, 10, 5, tzinfo=UTC))
    lines = path.read_text().splitlines()
    rec = json.loads(lines[2]); rec["p_up"] = 0.9
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    assert any("edited" in e for e in views.verify(str(path)))


@pytest.mark.parametrize("bad", [
    _sub(views=_sub()["views"][:5]),                                               # missing one
    _sub(views=_sub()["views"] + [{"instrument": "^NSEI", "p_up": 0.5, "reason": "x"}]),
    _sub(views=[dict(v, p_up=0.99) for v in _sub()["views"]]),                     # out of range
    _sub(views=[dict(v, p_up=True) for v in _sub()["views"]]),
    _sub(views=[dict(v, reason="x" * 201) for v in _sub()["views"]]),
    _sub(run_id=""),
])
def test_invalid_submissions_are_rejected_and_nothing_is_written(tmp_path, bad):
    path = str(tmp_path / "v.jsonl")
    with pytest.raises(ValueError):
        views.append_views(path, bad)
    assert views.read_views(path) == []


def test_cli_reports_rejection_with_exit_code_1(tmp_path, capsys):
    path = str(tmp_path / "v.jsonl")
    assert views.main(["append", "--file", path, "--json", json.dumps(_sub(views=[]))]) == 1
    assert "REJECTED" in capsys.readouterr().err
    assert views.main(["append", "--file", path, "--json", json.dumps(_sub())]) == 0
    assert views.main(["verify", "--file", path]) == 0
