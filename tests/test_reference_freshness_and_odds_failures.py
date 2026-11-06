"""Pruebas del arreglo del incidente del 2026-10-26 → 2026-11-06 (odds-api.io
movió Bet365 a planes de pago → HTTP 403 en todos los lotes de cuotas → 0 picks
sin ningún aviso) y del cambio de referencia a Unibet:

  1. Filtro de frescura de la referencia (value_detection.max_reference_age_hours).
  2. OddsApiError distingue fallos sistémicos (401/402/403) de puntuales.
  3. Los lotes de cuotas fallidos quedan registrados y se avisan por Telegram.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import (
    AppConfig,
    DailyConfig,
    OddsProviderConfig,
    ValueDetectionConfig,
    load_config,
)
from valuebet.daily import OddsFetchDiagnostics, _fetch_odds_in_batches, generate_daily_picks
from valuebet.daily_job import run_daily_job
from valuebet.kelly import BankrollLimits
from valuebet.models import BookmakerMarket, Event, Outcome
from valuebet.odds_provider import OddsApiError, OddsApiIoProvider
from valuebet.storage.db import Storage
from valuebet.value_finder import find_value_bets_in_event

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _event(ref_updated_at, ref_name="Unibet", extra_refs=None) -> Event:
    ref = BookmakerMarket(
        bookmaker=ref_name,
        market_key="h2h",
        updated_at=ref_updated_at,
        outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
    )
    betplay = BookmakerMarket(
        bookmaker="Betplay",
        market_key="h2h",
        updated_at=NOW,
        outcomes=[Outcome("home", 2.20), Outcome("draw", 3.30), Outcome("away", 3.80)],
    )
    books = {ref_name: [ref], "Betplay": [betplay]}
    books.update(extra_refs or {})
    return Event(
        event_id="e1",
        sport="football",
        league="Primera A",
        home_team="Millonarios",
        away_team="Nacional",
        commence_time=NOW + timedelta(days=1),
        bookmakers=books,
    )


def _find(event, refs=("Unibet",), **kw):
    return find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=list(refs),
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
        now=NOW,
        **kw,
    )


# ---------------------------------------------------------------- frescura
def test_fresh_reference_is_used():
    event = _event(NOW - timedelta(hours=3))
    assert len(_find(event, max_reference_age_hours=48)) == 1


def test_stale_reference_is_ignored_and_logged():
    event = _event(NOW - timedelta(days=5))
    log = []
    assert _find(event, max_reference_age_hours=48, stale_reference_log=log) == []
    assert log and "Unibet" in log[0] and "120 h" in log[0]


def test_stale_filter_disabled_with_none():
    event = _event(NOW - timedelta(days=30))
    assert len(_find(event, max_reference_age_hours=None)) == 1


def test_reference_without_updated_at_is_never_filtered():
    """Pinnacle (pinnapi.com) no trae fecha: no hay base para descartarla."""
    event = _event(None, ref_name="Pinnacle")
    assert len(_find(event, refs=("Pinnacle",), max_reference_age_hours=1)) == 1


def test_naive_datetimes_are_treated_as_utc():
    event = _event((NOW - timedelta(days=5)).replace(tzinfo=None))
    assert _find(event, max_reference_age_hours=48) == []


def test_stale_first_reference_falls_back_to_fresh_second_reference():
    fresh_ref = BookmakerMarket(
        bookmaker="OtraCasa",
        market_key="h2h",
        updated_at=NOW - timedelta(hours=1),
        outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
    )
    event = _event(NOW - timedelta(days=5), extra_refs={"OtraCasa": [fresh_ref]})
    results = _find(event, refs=("Unibet", "OtraCasa"), max_reference_age_hours=48)
    assert len(results) == 1
    assert results[0].reference_bookmakers == ["OtraCasa"]


# ------------------------------------------------------------------ config
def _write_cfg(tmp, extra=""):
    p = Path(tmp) / "config.yaml"
    p.write_text(
        "bankroll:\n  total: 1000000\n"
        "odds_provider:\n  api_key: x\n  target_bookmakers: [Betplay]\n  reference_bookmakers: [Unibet]\n  sports: [football]\n"
        "alerts:\n  telegram:\n    enabled: false\n    bot_token: x\n    chat_id: x\n" + extra
    )
    return str(p)


def test_config_max_reference_age_default_custom_and_null():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_config(_write_cfg(tmp)).value_detection.max_reference_age_hours == 48.0
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config(_write_cfg(tmp, "value_detection:\n  max_reference_age_hours: 12\n"))
        assert cfg.value_detection.max_reference_age_hours == 12.0
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config(_write_cfg(tmp, "value_detection:\n  max_reference_age_hours: null\n"))
        assert cfg.value_detection.max_reference_age_hours is None


def test_example_config_no_longer_requests_bet365():
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.example.yaml").read_text())
    op = raw["odds_provider"]
    assert op["reference_bookmakers"] == ["Unibet"]
    assert op["target_bookmakers"] == ["Betplay"]
    assert "Bet365" not in op["target_bookmakers"] + op["reference_bookmakers"]
    assert raw["value_detection"]["max_reference_age_hours"] == 48


# ------------------------------------------------------------ OddsApiError
def _resp(status, text=""):
    import requests

    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = {}
    r.json.return_value = {}
    r.raise_for_status.side_effect = requests.HTTPError(response=r) if status >= 400 else None
    return r


def test_403_raises_systemic_odds_api_error_with_body():
    provider = OddsApiIoProvider(api_key="k")
    body = '{"error":"Bet365 is only available on our paid plans."}'
    with patch.object(provider._session, "get", return_value=_resp(403, body)) as g:
        with pytest.raises(OddsApiError) as ei:
            provider._get("/odds/multi", {"bookmakers": "Bet365,Betplay"})
    assert g.call_count == 1  # 4xx no se reintenta
    assert ei.value.status_code == 403
    assert ei.value.is_systemic
    assert "paid plans" in ei.value.body
    assert isinstance(ei.value, RuntimeError)  # compatibilidad con el código existente


def test_500_is_not_systemic():
    provider = OddsApiIoProvider(api_key="k", max_retries=2)
    with patch.object(provider._session, "get", return_value=_resp(500, "boom")), patch(
        "valuebet.odds_provider.time.sleep"
    ):
        with pytest.raises(OddsApiError) as ei:
            provider._get("/events")
    assert ei.value.status_code == 500
    assert not ei.value.is_systemic


# ------------------------------------------------------- lotes + diagnóstico
class _BatchProvider:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)  # por llamada: lista de Events, o excepción
        self.calls = []

    def get_events_odds(self, event_ids, bookmakers, sport=None):
        self.calls.append(list(event_ids))
        b = self.behaviors[len(self.calls) - 1]
        if isinstance(b, Exception):
            raise b
        return b


def _stub_events(n):
    return [
        Event(event_id=str(i), sport="football", league="L", home_team="a", away_team="b",
              commence_time=NOW, bookmakers={})
        for i in range(n)
    ]


def test_systemic_error_aborts_remaining_batches():
    provider = _BatchProvider([OddsApiError("x", status_code=403, body="Bet365 paid")])
    diag = OddsFetchDiagnostics()
    ids = [str(i) for i in range(35)]  # 4 lotes
    events = _fetch_odds_in_batches(provider, ids, ["Betplay", "Bet365"], "football", diag)
    assert events == []
    assert len(provider.calls) == 1  # no gasta cuota en los 3 lotes restantes
    assert diag.failed_batches == 1 and diag.systemic_error
    assert "403" in diag.systemic_error and "Bet365 paid" in diag.systemic_error
    assert diag.has_problem()
    assert "plan" in diag.alert_text()


def test_transient_error_skips_only_that_batch():
    provider = _BatchProvider([_stub_events(10), OddsApiError("x", status_code=500), _stub_events(5)])
    diag = OddsFetchDiagnostics()
    ids = [str(i) for i in range(25)]
    events = _fetch_odds_in_batches(provider, ids, ["Betplay"], "football", diag)
    assert len(events) == 15
    assert len(provider.calls) == 3
    assert diag.failed_batches == 1 and diag.total_batches == 3
    assert diag.systemic_error is None
    assert diag.has_problem()
    assert "1 de 3" in diag.alert_text()


def test_clean_run_has_no_problem():
    provider = _BatchProvider([_stub_events(3)])
    diag = OddsFetchDiagnostics(events_listed=3)
    events = _fetch_odds_in_batches(provider, ["1", "2", "3"], ["Betplay"], "football", diag)
    diag.events_with_odds = len(events)
    assert not diag.has_problem()


# ------------------------------------------------- generate_daily_picks / job
class _Provider403:
    def __init__(self):
        self.odds_calls = 0

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        return _stub_events(12)

    def get_events_odds(self, event_ids, bookmakers, sport=None):
        self.odds_calls += 1
        raise OddsApiError("403", status_code=403, body="Bet365 is only available on our paid plans.")

    def get_event_result(self, event_id):
        raise NotImplementedError


def _cfg(db_path, output_dir, sports=("football",)):
    return AppConfig(
        bankroll=BankrollLimits(total=1_000_000),
        odds_provider=OddsProviderConfig(
            name="odds_api_io", api_key="x", base_url="https://x",
            target_bookmakers=["Betplay"], reference_bookmakers=["Bet365"], sports=list(sports),
        ),
        value_detection=ValueDetectionConfig(),
        daily=DailyConfig(num_picks=10, max_picks_per_event=1),
        telegram=None, db_path=db_path, output_dir=output_dir, log_level="INFO", log_file=None,
    )


def test_generate_daily_picks_reports_403_and_skips_next_sport():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        provider = _Provider403()
        diag = OddsFetchDiagnostics()
        picks = generate_daily_picks(
            _cfg("", tmp, sports=("football", "basketball")), provider, storage, diagnostics=diag
        )
    assert picks == []
    assert provider.odds_calls == 1  # el 2.º deporte no vuelve a pedir lo mismo
    assert diag.systemic_error and diag.has_problem()


def test_run_daily_job_sends_odds_problem_alert_before_picks_message():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        alerter = MagicMock()
        run_daily_job(_cfg(str(Path(tmp) / "t.db"), tmp), _Provider403(), storage, alerter=alerter)
    alerter.send_odds_problem_alert.assert_called_once()
    assert "plan" in alerter.send_odds_problem_alert.call_args[0][0]
    names = [c[0] for c in alerter.method_calls]
    assert names.index("send_odds_problem_alert") < names.index("send_daily_picks_message")


def test_run_daily_job_no_alert_when_odds_fetch_is_fine():
    class _OkProvider(_Provider403):
        def get_events_odds(self, event_ids, bookmakers, sport=None):
            return _stub_events(len(event_ids))

    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        alerter = MagicMock()
        run_daily_job(_cfg(str(Path(tmp) / "t.db"), tmp), _OkProvider(), storage, alerter=alerter)
    alerter.send_odds_problem_alert.assert_not_called()


# --------------------------------- parseo con forma REAL de Unibet (2026-11-06)
def test_parse_event_with_real_unibet_shapes_and_odd_markets():
    raw = {
        "id": 76084766, "home": "Fortaleza FC", "away": "Independiente Medellin",
        "date": "2026-11-07T00:00:00Z", "status": "pending",
        "league": {"name": "Liga", "slug": "colombia-liga-dimayor-finalizacion"},
        "bookmakers": {
            "Betplay": [
                {"name": "ML", "updatedAt": "2026-11-06T03:40:00Z", "odds": [{"home": "3.40", "draw": "3.50", "away": "2.10"}]},
            ],
            "Unibet": [
                {"name": "ML", "updatedAt": "2026-11-05T08:59:44.566Z", "odds": [{"home": "3.35", "draw": "3.50", "away": "2.08"}]},
                {"name": "Double Chance", "updatedAt": "2026-11-05T08:59:44.566Z", "odds": [{"1X": "1.65", "12": "1.26", "X2": "1.28"}]},
                {"name": "Totals", "updatedAt": "2026-11-06T01:13:30.71Z", "odds": [
                    {"hdp": 2.5, "over": "1.90", "under": "1.78"}, {"hdp": 3.5, "over": "3.35", "under": "1.27"}]},
                {"name": "Both Teams To Score", "updatedAt": "2026-11-03T11:35:14.118Z", "odds": [{"yes": "1.73", "no": "1.95"}]},
                # formas no verificadas: no deben tumbar el parseo del evento
                {"name": "Anytime Goalscorer", "updatedAt": "2026-11-06T01:13:00Z", "odds": ["raro", None, {"name": "Jugador", "price": "x"}]},
                {"name": "Correct Score", "odds": 5},
            ],
        },
    }
    ev = OddsApiIoProvider._parse_event(raw, sport="football")
    assert ev.event_id == "76084766"
    assert [m.market_key for m in ev.markets_for("Unibet", "totals")] == ["totals", "totals"]
    assert ev.markets_for("Unibet", "btts")[0].outcome_price("yes") == 1.73
    assert ev.markets_for("Unibet", "h2h")[0].updated_at.tzinfo is not None
    assert ev.markets_for("Betplay", "h2h")


def test_real_unibet_staleness_example_btts_dropped_h2h_kept_at_48h():
    """Caso real del 2026-11-06 ~04:00 UTC: h2h de Unibet de hace ~19 h pasa;
    su BTTS del 3-nov (~65 h) se ignora."""
    now = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)
    h2h = BookmakerMarket("Unibet", "h2h", datetime(2026, 11, 5, 8, 59, tzinfo=timezone.utc),
                          [Outcome("home", 3.35), Outcome("draw", 3.50), Outcome("away", 2.08)])
    btts = BookmakerMarket("Unibet", "btts", datetime(2026, 11, 3, 11, 35, tzinfo=timezone.utc),
                           [Outcome("yes", 1.73), Outcome("no", 1.95)])
    bp_h2h = BookmakerMarket("Betplay", "h2h", now, [Outcome("home", 3.90), Outcome("draw", 3.50), Outcome("away", 2.08)])
    bp_btts = BookmakerMarket("Betplay", "btts", now, [Outcome("yes", 2.00), Outcome("no", 1.95)])
    ev = Event("1", "football", "L", "A", "B", now + timedelta(hours=20),
               bookmakers={"Unibet": [h2h, btts], "Betplay": [bp_h2h, bp_btts]})
    res = find_value_bets_in_event(ev, ["Betplay"], ["Unibet"], "multiplicative", 1.0, 1,
                                   max_reference_age_hours=48, now=now)
    assert {r.market_key for r in res} == {"h2h"}
