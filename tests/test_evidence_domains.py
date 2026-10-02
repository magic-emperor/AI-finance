import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import evidence_domains as ed  # noqa: E402


def _flag(fid, kind, *urls):
    return {"flag_id": fid, "kind": kind, "evidence": [{"url": u} for u in urls]}


def _write(tmp_path, day, flags):
    d = tmp_path / "archive" / "flags"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.jsonl").write_text("\n".join(json.dumps(f) for f in flags) + "\n", encoding="utf-8")


def test_host_and_allowlist_matching():
    assert ed.host_of("https://WWW.NSEINDIA.com/api/x?y=1") == "www.nseindia.com"
    assert ed.host_of("not a url") is None
    assert ed.allowed("www.nseindia.com", ed.PLAN_ALLOWLIST) == "*.nseindia.com"
    assert ed.allowed("nseindia.com", ed.PLAN_ALLOWLIST) == "*.nseindia.com"
    assert ed.allowed("evilnseindia.com", ed.PLAN_ALLOWLIST) is None
    assert ed.allowed("www.fxstreet.com", ed.PLAN_ALLOWLIST) is None


def test_window_and_tally(tmp_path):
    _write(tmp_path, "2026-07-01", [_flag("old", "announcement", "https://old.example.com/a")])
    _write(tmp_path, "2026-10-01", [
        _flag("a", "bulk_deal_buy", "https://www.nseindia.com/x", "https://www.nseindia.com/y"),
        _flag("b", "news_multi_source", "https://www.fxstreet.com/n"),
        {"flag_id": "c", "kind": "macro_move", "evidence": [{"claim": "no url"}]},
    ])
    flags = ed.read_flags_dir(str(tmp_path), *_window("2026-10-02", 60))
    assert {f["flag_id"] for _, f in flags} == {"a", "b", "c"}  # July file is outside 60 days
    rows = ed.tally(flags)
    assert rows["www.nseindia.com"]["urls"] == 2
    assert len(rows["www.nseindia.com"]["flags"]) == 1
    assert "(no url)" in rows
    out = ed.render(rows, len(flags), ["2026-10-01.jsonl"], ed.PLAN_ALLOWLIST)
    assert "Hosts not covered by the plan allowlist: www.fxstreet.com" in out


def _window(today, days):
    from datetime import datetime, timedelta, timezone
    end = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return end - timedelta(days=days - 1), end
