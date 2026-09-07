"""Tests de src/valuebet/pinnapi_provider.py — Pinnacle vía pinnapi.com
(fuente no oficial, opcional). Cubren la forma REAL confirmada con
scripts/verify_pinnapi.py (2026-09-07): mercados anidados por período (solo
'num_0' = partido completo), sin mercado 'btts', y la lista blanca de ligas
que evita mezclar variantes de "Corners"/"Bookings"/mujeres/juveniles con el
partido real."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.models import Event
from valuebet.pinnapi_provider import (
    PinnapiProvider,
    attach_pinnacle_reference_markets,
    markets_from_pinnapi_event,
)


def _pinnapi_event(**overrides) -> dict:
    base = {
        "event_id": 1,
        "league_name": "England - Premier League",
        "starts": "2026-09-10T19:00:00Z",
        "home": "Arsenal",
        "away": "Chelsea",
        "event_type": "prematch",
        "periods": {
            "num_0": {
                "number": 0,
                "description": "Game",
                "money_line": {"home": 1.90, "draw": 3.60, "away": 4.20},
                "totals": {
                    "2.5": {"points": 2.5, "over": 2.06, "under": 1.81, "max": 450},
                    "3": {"points": 3, "over": 2.89, "under": 1.43, "max": 450},
                },
            },
            "num_1": {
                "number": 1,
                "description": "Half 1",
                "money_line": {"home": 3.21, "draw": 2.21, "away": 3.51},
            },
        },
    }
    base.update(overrides)
    return base


def _our_event(**overrides) -> Event:
    base = dict(
        event_id="evt1",
        sport="football",
        league="England - Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=datetime(2026, 9, 10, 19, 0, 0),
        bookmakers={},
    )
    base.update(overrides)
    return Event(**base)


def test_markets_from_pinnapi_event_builds_h2h_and_totals():
    markets = markets_from_pinnapi_event(_pinnapi_event())
    by_key = {m.market_key: m for m in markets}

    assert set(by_key) == {"h2h", "totals"}
    assert by_key["h2h"].bookmaker == "Pinnacle"
    assert by_key["h2h"].outcome_price("home") == 1.90
    assert by_key["h2h"].outcome_price("draw") == 3.60
    assert by_key["h2h"].outcome_price("away") == 4.20

    assert by_key["totals"].outcome_price("over_2.5") == 2.06
    assert by_key["totals"].outcome_price("under_2.5") == 1.81


def test_markets_from_pinnapi_event_whole_number_point_emits_both_name_formats():
    """El bug potencial que este test previene: pinnapi manda 'points': 3
    (int) para una línea entera. Si odds-api.io (para Betplay/Bet365) llega
    a formatear ese mismo punto como 'over_3.0' en vez de 'over_3', el cruce
    de nombres en value_finder.py (comparación por igualdad exacta de
    string) fallaría en silencio y Pinnacle jamás se usaría para esa línea.
    Se generan ambas variantes a propósito — ver el docstring de
    _totals_outcomes."""
    markets = markets_from_pinnapi_event(_pinnapi_event())
    totals = next(m for m in markets if m.market_key == "totals")

    assert totals.outcome_price("over_3") == 2.89
    assert totals.outcome_price("over_3.0") == 2.89
    assert totals.outcome_price("under_3") == 1.43
    assert totals.outcome_price("under_3.0") == 1.43


def test_markets_from_pinnapi_event_ignores_half_time_period():
    """Solo periods['num_0'] (partido completo) debe usarse — 'num_1' (Half
    1) tiene su propio money_line que NO debe filtrarse al mercado 'h2h' de
    tiempo completo."""
    markets = markets_from_pinnapi_event(_pinnapi_event())
    h2h = next(m for m in markets if m.market_key == "h2h")
    # Los valores de 'num_0', no los de 'num_1' (3.21/2.21/3.51).
    assert h2h.outcome_price("home") == 1.90


def test_markets_from_pinnapi_event_no_btts_market_produced():
    """Confirmado con datos reales: Pinnacle (vía pinnapi) no ofrece BTTS.
    Este módulo nunca debe inventar un mercado 'btts' — btts sigue
    dependiendo siempre de Bet365."""
    markets = markets_from_pinnapi_event(_pinnapi_event())
    assert "btts" not in {m.market_key for m in markets}


def test_markets_from_pinnapi_event_missing_periods_returns_empty():
    assert markets_from_pinnapi_event({"periods": {}}) == []
    assert markets_from_pinnapi_event({}) == []


def test_attach_pinnacle_reference_markets_matches_by_team_and_league_allowlist():
    events = [_our_event()]
    pinnapi_events = [_pinnapi_event()]

    matched = attach_pinnacle_reference_markets(events, pinnapi_events)

    assert matched == 1
    assert "Pinnacle" in events[0].bookmakers
    assert events[0].markets_for("Pinnacle", "h2h")[0].outcome_price("home") == 1.90


def test_attach_pinnacle_reference_markets_excludes_non_curated_league_variants():
    """Un partido de los MISMOS dos equipos pero listado bajo una liga fuera
    de la lista blanca (ej. "England - Premier League Corners", que en la
    práctica trae cuotas de córners, no de goles) NO debe emparejarse —
    mezclar esos mercados sería un bug silencioso grave."""
    events = [_our_event()]
    pinnapi_events = [_pinnapi_event(league_name="England - Premier League Corners")]

    matched = attach_pinnacle_reference_markets(events, pinnapi_events)

    assert matched == 0
    assert "Pinnacle" not in events[0].bookmakers


def test_attach_pinnacle_reference_markets_requires_team_name_match():
    events = [_our_event(home_team="Liverpool", away_team="Everton")]
    pinnapi_events = [_pinnapi_event()]  # Arsenal vs Chelsea

    matched = attach_pinnacle_reference_markets(events, pinnapi_events)

    assert matched == 0
    assert "Pinnacle" not in events[0].bookmakers


def test_attach_pinnacle_reference_markets_rejects_kickoff_too_far_apart():
    """Mismos equipos, misma liga, pero un kickoff a más de MAX_KICKOFF_DIFF
    de diferencia — probablemente un cruce de ida/vuelta distinto, no el
    mismo partido. No debe emparejar."""
    events = [_our_event(commence_time=datetime(2026, 9, 10, 19, 0, 0))]
    far_event = _pinnapi_event(starts="2026-10-01T19:00:00Z")
    pinnapi_events = [far_event]

    matched = attach_pinnacle_reference_markets(events, pinnapi_events)

    assert matched == 0


def test_attach_pinnacle_reference_markets_skips_non_football_events():
    events = [_our_event(sport="basketball")]
    pinnapi_events = [_pinnapi_event()]

    matched = attach_pinnacle_reference_markets(events, pinnapi_events)

    assert matched == 0
    assert "Pinnacle" not in events[0].bookmakers


def test_attach_pinnacle_reference_markets_does_not_overwrite_existing_bookmakers():
    events = [_our_event(bookmakers={"Bet365": []})]
    pinnapi_events = [_pinnapi_event()]

    attach_pinnacle_reference_markets(events, pinnapi_events)

    assert "Bet365" in events[0].bookmakers
    assert "Pinnacle" in events[0].bookmakers


def test_pinnapi_provider_rejects_placeholder_api_key():
    import pytest

    with pytest.raises(ValueError):
        PinnapiProvider(api_key="TU_PINNAPI_API_KEY_AQUI")


def test_pinnapi_provider_uses_x_portal_apikey_header_and_sport_id_1():
    """Confirmado real (2026-09-07): el header x-portal-apikey funcionó al
    primer intento y sport_id=1 es fútbol — no se prueban variantes acá
    (esas quedaron en scripts/verify_pinnapi.py para exploración manual)."""
    provider = PinnapiProvider(api_key="fake-key")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"sport_id": 1, "sport_name": "Soccer", "events": [_pinnapi_event()]}
    resp.raise_for_status.side_effect = None

    with patch.object(provider._session, "get", return_value=resp) as mock_get:
        events = provider.get_soccer_events()

    assert mock_get.call_args.kwargs["params"] == {"sport_id": 1, "event_type": "prematch"}
    assert provider._session.headers["x-portal-apikey"] == "fake-key"
    assert len(events) == 1
