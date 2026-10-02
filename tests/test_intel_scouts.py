"""
Tests for intel.scouts -- fixtures only, no network.

These pin the rules that decide what is allowed to wake the (costly) reasoning agent, and the
failure behaviour: a failed source is recorded and emits nothing; a failed fire leaves flags
pending instead of silently dropping them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from intel.ledger import read_records
from intel.scouts import escalate, filings, macro, market, news, run_scouts
from intel.scouts.common import ist_to_utc_iso, make_flag
from intel.validate import validate_ledger

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_calendar_fetch(monkeypatch):
    """run_scouts refreshes the NSE holiday calendar; tests must never touch the network."""
    monkeypatch.setattr(run_scouts.nse_calendar, "refresh", lambda *a, **k: None)


def test_ist_timestamps_convert_to_utc():
    assert ist_to_utc_iso("01-Oct-2026 11:26") == "2026-10-01T05:56:00Z"
    assert ist_to_utc_iso("23-SEP-2026") == "2026-09-22T18:30:00Z"
    assert ist_to_utc_iso("garbage") is None


def test_plain_symbols_get_ns_suffix_but_indices_and_fx_pass_through():
    f = lambda s: make_flag("k", s, s, 0.5, "x", [], "2026-10-01T00:00:00Z")["instrument"]
    assert f("RELIANCE") == "RELIANCE.NS"
    assert f("M&M") == "M&M.NS"
    assert f("^NSEI") == "^NSEI" and f("USDINR=X") == "USDINR=X"


# ── filings ───────────────────────────────────────────────────────────────────
def sast(**o):
    r = {"acqSaleType": "Acquisition", "acquirerName": "Clarus", "company": "TCPL Packaging Limited",
         "symbol": "TCPLPACK", "promoterType": "N", "totAcqShare": "1.5", "totAftShare": "6.2",
         "acquisitionMode": "Open Market", "application_no": "1", "timestamp": "01-Oct-2026 11:26",
         "acquirerDate": "29-SEP-2026 to 29-SEP-2026",
         "attachement": "https://nsearchives.nseindia.com/x.zip"}
    r.update(o)
    return r


def test_sast_flags_only_meaningful_buys():
    flags = filings.sast_flags([
        sast(application_no="1"),                                               # 5%+ holder adds 1.5pts
        sast(application_no="2", promoterType="Y", totAcqShare="1.0", totAftShare="40"),
        sast(application_no="3", totAcqShare="0.03", totAftShare="5.01"),       # dust
        sast(application_no="4", acqSaleType="Sale"),                           # sells ignored
        sast(application_no="5", totAcqShare="2", totAftShare="2.5"),           # never reached 5%
        sast(application_no="6", promoterType="Y", acquisitionMode="Inter-se transfer", totAcqShare="2"),
        sast(application_no="7", promoterType="Y", acquisitionMode="Preferential Allotment", totAcqShare="2"),
        sast(application_no="8", acquisitionMode="Others", totAcqShare="9", totAftShare="9"),
    ])
    ids = {f["extra"]["after_pct"] for f in flags}
    assert len(flags) == 2 and ids == {6.2, 40.0}
    promoter = next(f for f in flags if f["extra"]["promoter"])
    holder = next(f for f in flags if not f["extra"]["promoter"])
    assert promoter["importance"] > holder["importance"] >= 0.65
    assert all(f["direction_hint"] == "UP" for f in flags)


def test_deal_flags_buy_only_with_value_floor_and_scaling_importance():
    payload = {"as_on_date": "01-Oct-2026",
               "BULK_DEALS_DATA": [
                   {"buySell": "BUY", "clientName": "A", "name": "X Ltd", "symbol": "X", "qty": "1000000", "watp": "100", "date": "01-Oct-2026"},   # 10 cr
                   {"buySell": "BUY", "clientName": "B", "name": "Y Ltd", "symbol": "Y", "qty": "100000", "watp": "100", "date": "01-Oct-2026"},    # 1 cr
                   {"buySell": "SELL", "clientName": "C", "name": "Z Ltd", "symbol": "Z", "qty": "9000000", "watp": "100", "date": "01-Oct-2026"}, # sell
                   {"buySell": "BUY", "clientName": "D", "name": "W Ltd", "symbol": "W", "qty": "10000000", "watp": "100", "date": "01-Oct-2026"}, # 100 cr
               ],
               "BLOCK_DEALS_DATA": []}
    flags = filings.deal_flags(payload)
    assert sorted(f["symbol"] for f in flags) == ["W", "X"]
    imp = {f["symbol"]: f["importance"] for f in flags}
    assert imp["W"] > imp["X"] >= 0.3
    assert imp["W"] < escalate.ESCALATE_AT        # a deal alone can never wake the agent


def test_announcements_keep_informative_categories_only():
    rows = [{"desc": "Trading Window", "symbol": "AAA", "seq_id": "1", "an_dt": "01-Oct-2026 10:00:00"},
            {"desc": "Bagging/Receiving of orders/contracts", "symbol": "BBB", "seq_id": "2",
             "an_dt": "01-Oct-2026 10:00:00", "attchmntText": "Won Rs 500 cr order", "sm_name": "BBB Ltd"},
            {"desc": "Acquisition", "symbol": None, "seq_id": "3", "an_dt": "01-Oct-2026 10:00:00"}]
    flags = filings.announcement_flags(rows)
    assert [f["symbol"] for f in flags] == ["BBB"]


def test_filings_run_isolates_a_failing_source():
    class Sess:
        def get_json(self, url):
            if "sast" in url:
                raise RuntimeError("blocked")
            if "largedeal" in url:
                return {"BULK_DEALS_DATA": [], "BLOCK_DEALS_DATA": []}
            return []
    out = filings.run(Sess(), "29-09-2026", "01-10-2026")
    st = {s["source"]: s["status"] for s in out["sources"]}
    assert st["nse_sast_reg29"] == "FAILED" and st["nse_bulk_block_deals"] == "OK"
    assert out["flags"] == []


# ── news ──────────────────────────────────────────────────────────────────────
EQUITY_L = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n"
            "ADANIENT,Adani Enterprises Limited, EQ,01-01-2000\n"
            "RELIANCE,Reliance Industries Limited, EQ,01-01-2000\n"
            "TATA,Tata Motors Limited, EQ,01-01-2000\n")


def item(title, group, minutes_ago, publisher=None):
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": title, "link": f"https://x/{group}/{minutes_ago}", "publisher": publisher or group,
            "group": group, "published_at": ts}


def test_two_independent_publishers_corroborate_one_company():
    idx = news.build_company_index(EQUITY_L)
    flags = news.news_flags([item("Adani Enterprises wins airport bid", "times_group", 30),
                             item("Adani Enterprises shares surge after bid", "ht_media", 10)], idx)
    assert len(flags) == 1 and flags[0]["symbol"] == "ADANIENT"
    assert flags[0]["extra"]["n_groups"] == 2


def test_one_outlet_cannot_corroborate_itself():
    idx = news.build_company_index(EQUITY_L)
    flags = news.news_flags([item("Adani Enterprises wins bid", "network18", 30, "Moneycontrol"),
                             item("Adani Enterprises bid explained", "network18", 10, "CNBC-TV18")], idx)
    assert flags == []


def test_mentions_far_apart_in_time_do_not_corroborate():
    idx = news.build_company_index(EQUITY_L)
    flags = news.news_flags([item("Adani Enterprises wins bid", "times_group", 600),
                             item("Adani Enterprises shares rise", "ht_media", 5)], idx)
    assert flags == []


def test_ambiguous_one_word_names_are_not_matched():
    idx = news.build_company_index(EQUITY_L)
    assert news.mentions("Tata group stocks rally", idx) == []
    assert news.mentions("Reliance Industries Q2 preview", idx) == ["RELIANCE"]


# ── market ────────────────────────────────────────────────────────────────────
BHAV = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "AAA, EQ, 01-Oct-2026, 100, 101, 110, 100, 108, 108, 105, 500000, 6000, 100, 1, 1\n"
        "BBB, BE, 01-Oct-2026, 100, 101, 110, 100, 108, 108, 105, 500000, 6000, 100, 1, 1\n")


def hist(days=20, vol=100000, base=100.0):
    return [[vol, base + (0.2 if i % 2 else -0.2)] for i in range(days)]


def row(sym="AAA", close=108.0, prev=100.0, volume=500000.0, turnover=60.0):
    return {"symbol": sym, "close": close, "prev_close": prev, "volume": volume, "turnover_cr": turnover}


def test_bhavcopy_parses_eq_series_only():
    rows = market.parse_bhavcopy(BHAV)
    assert [r["symbol"] for r in rows] == ["AAA"] and rows[0]["turnover_cr"] == 60.0


def test_volume_spike_with_big_move_is_flagged_and_marked_reactive():
    (f,) = market.volume_spike_flags([row()], {"AAA": hist()}, "01-Oct-2026")
    assert f["kind"] == "volume_breakout" and f["direction_hint"] == "UP"
    assert f["extra"]["reactive_by_construction"] is True


@pytest.mark.parametrize("r,h", [
    (row(volume=200000), hist()),        # only 2x volume
    (row(turnover=2.0), hist()),         # below the liquidity floor
    (row(close=100.5), hist()),          # volume spike but an ordinary move
    (row(), hist(days=10)),              # not enough history
])
def test_non_qualifying_stocks_are_not_flagged(r, h):
    assert market.volume_spike_flags([r], {"AAA": h}, "01-Oct-2026") == []


def test_history_keeps_only_the_last_20_days():
    h = {}
    for _ in range(30):
        market.update_history(h, [row()])
    assert len(h["AAA"]) == 20


# ── macro ─────────────────────────────────────────────────────────────────────
def test_two_sigma_fx_move_is_flagged_quiet_one_is_not():
    quiet = [100 + 0.1 * (i % 3) for i in range(30)]
    spike = quiet[:-1] + [quiet[-2] * 1.03]
    flags = macro.move_flags({"USDINR=X": spike, "EURUSD=X": quiet}, "2026-10-01")
    assert [f["symbol"] for f in flags] == ["USDINR=X"]


def test_only_recent_regulator_releases_are_flagged():
    items = [{"title": "RBI repo rate", "link": "https://rbi/1", "publisher": "RBI",
              "published_at": (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
             {"title": "Old circular", "link": "https://rbi/2", "publisher": "RBI",
              "published_at": (NOW - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    flags = macro.regulator_flags(items, NOW)
    assert len(flags) == 1 and flags[0]["summary"].startswith("[RBI]")


# ── escalation ────────────────────────────────────────────────────────────────
def flag(kind, sym, imp, fid=None, observed_at="2026-10-01T05:00:00Z"):
    f = make_flag(kind, fid or f"{kind}{sym}", sym, imp, f"{kind} on {sym}",
                  [{"url": "https://u", "publisher": "P", "published_at": observed_at, "claim": "c"}],
                  observed_at)
    return f


def test_single_modest_flag_does_not_escalate_but_confluence_does():
    assert escalate.select([flag("announcement", "AAA", 0.6)], set()) == []
    groups = escalate.select([flag("announcement", "AAA", 0.6), flag("news_multi_source", "AAA", 0.5)], set())
    assert len(groups) == 1 and groups[0]["importance"] == pytest.approx(0.75)
    assert groups[0]["kinds"] == ["announcement", "news_multi_source"]


def test_same_kind_twice_is_not_confluence():
    assert escalate.select([flag("announcement", "AAA", 0.6, "a"), flag("announcement", "AAA", 0.6, "b")], set()) == []


def test_already_escalated_flags_are_never_resent():
    f = flag("sast_acquisition", "AAA", 0.85)
    assert len(escalate.select([f], set())) == 1
    assert escalate.select([f], {f["flag_id"]}) == []


def test_throttle_daily_cap_and_min_gap():
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert escalate.throttle_ok([], NOW)[0]
    assert not escalate.throttle_ok([iso(NOW - timedelta(minutes=5))], NOW)[0]
    assert escalate.throttle_ok([iso(NOW - timedelta(hours=1))], NOW)[0]
    many = [iso(NOW - timedelta(hours=1 + i)) for i in range(8)]
    ok, why = escalate.throttle_ok(many, NOW)
    assert not ok and "daily cap" in why


def test_payload_text_carries_flag_ids_and_is_size_capped():
    groups = escalate.group_flags([flag("sast_acquisition", f"S{i}", 0.9, f"id{i}") for i in range(200)])
    text = escalate.build_text(groups, NOW)
    assert len(text) <= escalate.MAX_TEXT_CHARS and "flag_id" in text


def test_fire_posts_documented_headers_and_returns_session_url():
    sent = {}

    class Resp:
        status_code = 200
        text = ""
        def json(self): return {"claude_code_session_url": "https://claude.ai/code/session_1"}

    class Http:
        def post(self, url, **kw):
            sent.update(url=url, **kw)
            return Resp()

    url = escalate.fire("https://api.anthropic.com/v1/claude_code/routines/trig_1/fire", "tok", "hello", Http())
    assert url.endswith("session_1")
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert sent["headers"]["anthropic-beta"] == escalate.FIRE_BETA
    assert json.loads(sent["data"]) == {"text": "hello"}


def test_fire_failure_raises():
    class Resp:
        status_code = 401
        text = "unauthorized"
    class Http:
        def post(self, *a, **k): return Resp()
    with pytest.raises(RuntimeError, match="401"):
        escalate.fire("u", "t", "x", Http())


# ── orchestrator ──────────────────────────────────────────────────────────────
@pytest.fixture
def patched(monkeypatch):
    # Stamped "now": run_scouts drops pending flags older than 24h against the real clock,
    # so a fixed date here made these tests start failing one day after they were written.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    big = flag("sast_acquisition", "AAA", 0.9, "big1", observed_at=now)
    monkeypatch.setattr(run_scouts.filings, "run", lambda *a, **k: {
        "flags": [big], "sources": [{"source": "nse_sast_reg29", "status": "OK", "n_items": 1}]})
    for mod in (run_scouts.news,):
        monkeypatch.setattr(mod, "run", lambda *a, **k: {
            "flags": [], "sources": [{"source": "rss_x", "status": "OK", "n_items": 5}]})
    monkeypatch.setattr(run_scouts.macro, "run", lambda *a, **k: {
        "flags": [], "sources": [{"source": "feed_rbi", "status": "OK", "n_items": 5}]})
    monkeypatch.setattr(run_scouts.market, "run", lambda *a, **k: {
        "flags": [], "sources": [{"source": "nse_bhavcopy", "status": "OK", "n_items": 1}]})
    monkeypatch.delenv("AGENT_FIRE_URL", raising=False)
    monkeypatch.delenv("AGENT_FIRE_TOKEN", raising=False)
    return big


def test_unconfigured_fire_queues_and_says_so_without_pretending(tmp_path, patched, capsys):
    assert run_scouts.main(["--data-dir", str(tmp_path)]) == 0
    (archived,) = [json.loads(l) for p in (tmp_path / "archive" / "flags").glob("*.jsonl") for l in p.read_text().splitlines()]
    assert archived["seen_at"] >= archived["observed_at"]          # grader v2 enters no earlier than this
    state = json.loads((tmp_path / "state" / "scout_state.json").read_text())
    assert state["escalated"] == {} and len(state["pending"]) == 1
    run = read_records(str(tmp_path / "ledger"), "runs")[-1]
    assert "not configured" in run["notes"] and run["status"] == "OK"
    assert validate_ledger(str(tmp_path / "ledger")) == []


def test_successful_fire_marks_escalated_and_second_run_is_quiet(tmp_path, patched, monkeypatch):
    monkeypatch.setenv("AGENT_FIRE_URL", "https://example/fire")
    monkeypatch.setenv("AGENT_FIRE_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(run_scouts.escalate, "fire", lambda url, tok, text: calls.append(text) or "https://s/1")
    run_scouts.main(["--data-dir", str(tmp_path)])
    assert len(calls) == 1 and "AAA" in calls[0]
    state = json.loads((tmp_path / "state" / "scout_state.json").read_text())
    assert "big1" in state["escalated"] or len(state["escalated"]) == 1
    assert state["pending"] == [] and len(state["fires"]) == 1
    run_scouts.main(["--data-dir", str(tmp_path)])
    assert len(calls) == 1                                             # nothing re-sent
    runs = read_records(str(tmp_path / "ledger"), "runs")
    assert [r["status"] for r in runs] == ["OK", "NO_NEW_INFORMATION"]
    assert (tmp_path / "archive" / "flags").exists()


def test_failed_fire_keeps_flags_pending_and_is_recorded(tmp_path, patched, monkeypatch):
    monkeypatch.setenv("AGENT_FIRE_URL", "https://example/fire")
    monkeypatch.setenv("AGENT_FIRE_TOKEN", "tok")
    def boom(*a, **k):
        raise RuntimeError("fire failed: HTTP 500")
    monkeypatch.setattr(run_scouts.escalate, "fire", boom)
    run_scouts.main(["--data-dir", str(tmp_path)])
    state = json.loads((tmp_path / "state" / "scout_state.json").read_text())
    assert state["escalated"] == {} and len(state["pending"]) == 1 and state["fires"] == []
    run = read_records(str(tmp_path / "ledger"), "runs")[-1]
    assert run["status"] == "DEGRADED" and "FAILED" in run["notes"]


def test_all_sources_failing_is_a_failed_run_with_exit_code_2(tmp_path, monkeypatch):
    dead = lambda *a, **k: {"flags": [], "sources": [{"source": "s", "status": "FAILED", "n_items": 0, "error": "down"}]}
    for mod in (run_scouts.filings, run_scouts.news, run_scouts.macro, run_scouts.market):
        monkeypatch.setattr(mod, "run", dead)
    assert run_scouts.main(["--data-dir", str(tmp_path)]) == 2
    assert read_records(str(tmp_path / "ledger"), "runs")[-1]["status"] == "FAILED"


def test_bulk_deal_plus_volume_spike_is_one_event_not_two_signals():
    deal = flag("bulk_deal_buy", "AAA", 0.55)
    spike = flag("volume_breakout", "AAA", 0.60)
    assert escalate.select([deal, spike], set()) == []          # same family: no confluence boost
    media = flag("news_multi_source", "AAA", 0.45)
    (g,) = escalate.select([deal, spike, media], set())          # an independent family does count
    assert g["importance"] == pytest.approx(0.75)


def test_only_open_market_purchases_are_flagged_from_sast():
    base = dict(promoterType="Y", totAcqShare="2", totAftShare="40")
    modes = ["Open Market", "Inter-se transfer", "Preferential Allotment", "Others", ""]
    flagged = [m for m in modes if filings.sast_flags([sast(acquisitionMode=m, **base)])]
    assert flagged == ["Open Market"]


def test_volume_spike_alone_can_never_reach_the_escalation_threshold():
    (f,) = market.volume_spike_flags([row(close=130.0, volume=50_000_000.0)], {"AAA": hist()}, "01-Oct-2026")
    assert f["importance"] <= market.MAX_ALONE < escalate.ESCALATE_AT
    assert escalate.select([f], set()) == []


def test_spurt_in_volume_notice_is_tape_not_independent_corroboration():
    notice = filings.announcement_flags([{"desc": "Spurt in Volume", "symbol": "AAA", "seq_id": "9",
                                          "an_dt": "01-Oct-2026 10:00:00", "attchmntText": "volume up"}])
    assert notice[0]["kind"] == "volume_spurt_notice"
    spike = flag("volume_breakout", "AAA", 0.65)
    assert escalate.select(notice + [spike], set()) == []        # same event seen twice
    order = filings.announcement_flags([{"desc": "Bagging/Receiving of orders/contracts", "symbol": "AAA",
                                         "seq_id": "10", "an_dt": "01-Oct-2026 10:00:00", "attchmntText": "order"}])
    assert len(escalate.select(order + [spike], set())) == 1     # a real disclosure is independent
