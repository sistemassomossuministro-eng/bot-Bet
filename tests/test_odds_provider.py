"""OddsApiIoProvider._get(): manejo de errores HTTP, sin red real (mockeado).

Se agregó después de un caso real: un 400 Bad Request en /odds/multi por un
bookmaker no soportado por el proveedor tardó 3 reintentos con backoff
exponencial en fallar, y el log solo mostraba "400 Bad Request" sin el cuerpo
de la respuesta (que traía el motivo exacto). Estos tests fijan el
comportamiento correcto: no reintentar errores 4xx que no son 429, y loguear
el cuerpo de la respuesta para que el motivo quede en el log de GitHub
Actions sin tener que reproducir el problema a mano.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.odds_provider import OddsApiIoProvider, _normalize_market_key, _parse_odds_line

MINIMAL_EVENT_TEMPLATE = {
    "sport": {"name": "football"},
    "league": {"name": "x"},
    "home": "Local",
    "away": "Visita",
    "date": "2026-08-25T20:00:00Z",
    "status": "pending",
    "bookmakers": {},
}


def _event_json(event_id: str) -> dict:
    return dict(MINIMAL_EVENT_TEMPLATE, id=event_id)


def _make_response(status_code, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    if status_code >= 400:
        import requests

        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.side_effect = None
    return resp


def test_get_returns_json_on_success():
    provider = OddsApiIoProvider(api_key="fake-key")
    ok_resp = _make_response(200, {"hello": "world"})

    with patch.object(provider._session, "get", return_value=ok_resp) as mock_get:
        data = provider._get("/events", {"sport": "football"})

    assert data == {"hello": "world"}
    assert mock_get.call_count == 1


def test_get_does_not_retry_on_400_and_logs_body():
    provider = OddsApiIoProvider(api_key="fake-key")
    bad_resp = _make_response(400, text='{"error":"Unsupported bookmaker: Wplay"}')

    with patch.object(provider._session, "get", return_value=bad_resp) as mock_get, \
         patch("valuebet.odds_provider.logger") as mock_logger:
        try:
            provider._get("/odds/multi", {"eventIds": "1,2", "bookmakers": "Wplay,Betplay"})
            assert False, "se esperaba que _get lanzara una excepción"
        except RuntimeError:
            pass

    # Un 400 no es transitorio (no es un problema de red ni de rate limit) —
    # reintentarlo 3 veces solo demora el fallo sin cambiar el resultado.
    assert mock_get.call_count == 1

    # El cuerpo de la respuesta (con el motivo real del error) debe haber
    # quedado en el log, no solo "400 Bad Request".
    logged_text = " ".join(str(call) for call in mock_logger.warning.call_args_list)
    assert "Unsupported bookmaker" in logged_text


def test_get_retries_on_429_rate_limit():
    provider = OddsApiIoProvider(api_key="fake-key")
    rate_limited = _make_response(429, text="rate limited")
    rate_limited.headers = {"x-ratelimit-reset": "0"}
    ok_resp = _make_response(200, {"ok": True})

    with patch.object(provider._session, "get", side_effect=[rate_limited, ok_resp]) as mock_get, \
         patch("valuebet.odds_provider.time.sleep"):
        data = provider._get("/events")

    assert data == {"ok": True}
    assert mock_get.call_count == 2


def test_parse_odds_line_totals_without_hdp_is_discarded():
    """Bug real de producción: una línea de 'totals' sin valor de línea
    colapsaría distintas líneas de gol (0.5, 2.5, 8.5, ...) al mismo nombre
    de resultado ('over'/'under') y terminaría comparando cuotas de líneas
    totalmente distintas entre sí. Se descarta la línea entera en vez de
    arriesgar eso."""
    line = {"over": "1.90", "under": "1.90"}  # sin 'hdp' ni 'point'
    assert _parse_odds_line(line, market_key="totals") == []
    assert _parse_odds_line(line, market_key="spreads") == []


def test_parse_odds_line_totals_with_hdp_works():
    """El campo real de odds-api.io para el valor de línea es 'hdp', NO
    'point' — confirmado contra la documentación oficial. Reproduce
    exactamente la forma de línea vista en producción (con 'hdp')."""
    line = {"over": "1.90", "under": "1.90", "hdp": 2.5}
    outcomes = _parse_odds_line(line, market_key="totals")
    names = {o.name for o in outcomes}
    assert names == {"over_2.5", "under_2.5"}
    # 'hdp' no debe colarse como si fuera un resultado apostable más.
    assert "hdp_2.5" not in names


def test_parse_odds_line_ignores_link_and_max_fields():
    """Reproduce una línea con la forma completa real de la API (incluyendo
    los campos de link directo al bookmaker y el tope de apuesta 'max') —
    ninguno de esos debe terminar como un Outcome falso."""
    line = {
        "over": "2.000", "under": "1.800", "hdp": 3,
        "overLink": "https://example.com/over", "underLink": "https://example.com/under",
        "max": 500, "updatedAt": "2026-08-22T23:40:00Z",
    }
    outcomes = _parse_odds_line(line, market_key="totals")
    names = {o.name for o in outcomes}
    assert names == {"over_3", "under_3"}


def test_parse_odds_line_totals_still_accepts_point_as_fallback():
    # Alias de respaldo, por si algún bookmaker manda 'point' en vez de 'hdp'.
    line = {"over": "1.90", "under": "1.90", "point": 2.5}
    outcomes = _parse_odds_line(line, market_key="totals")
    names = {o.name for o in outcomes}
    assert names == {"over_2.5", "under_2.5"}


def test_parse_odds_line_h2h_without_hdp_is_unaffected():
    # h2h (home/draw/away) nunca lleva 'hdp' — no debe descartarse por eso.
    line = {"home": "1.95", "draw": "3.60", "away": "4.20"}
    outcomes = _parse_odds_line(line, market_key="h2h")
    names = {o.name for o in outcomes}
    assert names == {"home", "draw", "away"}


def test_normalize_market_key_totals_and_spread_match_real_api_names():
    """Nombres de mercado confirmados contra la documentación real de
    odds-api.io. 'Spread' (singular) es el caso importante: sin el alias
    explícito, el fallback genérico lo normaliza a 'spread' (singular), que
    NO calza con 'spreads' (plural) usado en el resto del código — la línea
    quedaría descartada en silencio por no matchear ningún mercado permitido."""
    assert _normalize_market_key("Totals") == "totals"
    assert _normalize_market_key("Spread") == "spreads"
    assert _normalize_market_key("ML") == "h2h"


def test_normalize_market_key_btts():
    """'Both Teams To Score' confirmado vía GET /markets?sport=football
    (respuesta real pegada por el usuario, ago-2026): existe, shape 'yesno'."""
    assert _normalize_market_key("Both Teams To Score") == "btts"
    # Las variantes de medio tiempo/2do tiempo se dejan sin mapear a propósito
    # (no se pidieron) — deben caer al fallback genérico, no a "btts".
    assert _normalize_market_key("Both Teams To Score HT") != "btts"


def test_parse_odds_line_btts_yes_no():
    """Mercado 'yesno': sin 'hdp' (es una proposición fija, no una línea con
    punto) — no debe descartarse como pasaría con 'totals'/'spreads'."""
    line = {"yes": "1.85", "no": "1.95"}
    outcomes = _parse_odds_line(line, market_key="btts")
    names = {o.name for o in outcomes}
    assert names == {"yes", "no"}


def test_parse_odds_line_btts_normalizes_case():
    """Por si el bookmaker manda 'Yes'/'No' en vez de 'yes'/'no' — el resto
    del código (settlement.py, descriptions.py) espera minúsculas."""
    line = {"Yes": "1.85", "No": "1.95"}
    outcomes = _parse_odds_line(line, market_key="btts")
    names = {o.name for o in outcomes}
    assert names == {"yes", "no"}


def test_list_events_with_multiple_leagues_makes_one_call_per_league():
    """Bug real de producción (ago-2026): GET /events?league=a,b,c devolvía
    404 'League not found' — el parámetro 'league' de ese endpoint solo
    acepta UN string (confirmado contra el spec oficial de odds-api.io), no
    una lista separada por comas. Con 15 ligas de fútbol curadas activas,
    esto dejaba el resumen diario sin NINGÚN evento. El fix: una llamada a
    /events por liga, combinando los resultados."""
    provider = OddsApiIoProvider(api_key="fake-key")
    leagues = ["colombia-liga-dimayor-finalizacion", "england-premier-league", "spain-laliga"]

    responses = [
        _make_response(200, [_event_json("evt-co-1")]),
        _make_response(200, [_event_json("evt-eng-1")]),
        _make_response(200, [_event_json("evt-esp-1")]),
    ]

    with patch.object(provider._session, "get", side_effect=responses) as mock_get:
        events = provider.list_events(sport="football", leagues=leagues)

    assert mock_get.call_count == 3
    called_leagues = [call.kwargs["params"]["league"] for call in mock_get.call_args_list]
    # Cada llamada debe llevar UNA sola liga (nunca una lista unida por comas).
    assert called_leagues == leagues
    assert {e.event_id for e in events} == {"evt-co-1", "evt-eng-1", "evt-esp-1"}


def test_list_events_with_multiple_leagues_dedupes_by_event_id():
    """Si (por lo que sea) el mismo evento apareciera en la respuesta de dos
    ligas distintas, no debe duplicarse en el resultado final."""
    provider = OddsApiIoProvider(api_key="fake-key")
    leagues = ["liga-a", "liga-b"]

    responses = [
        _make_response(200, [_event_json("evt-shared"), _event_json("evt-a")]),
        _make_response(200, [_event_json("evt-shared")]),
    ]

    with patch.object(provider._session, "get", side_effect=responses):
        events = provider.list_events(sport="football", leagues=leagues)

    assert {e.event_id for e in events} == {"evt-shared", "evt-a"}


def test_list_events_one_league_failing_does_not_block_the_others():
    """Si una liga individual falla (404, timeout, etc.), las demás ligas
    deben seguir consultándose — no se debe perder el resumen diario
    completo por un solo slug de liga con problemas."""
    provider = OddsApiIoProvider(api_key="fake-key")
    leagues = ["liga-rota", "liga-ok"]

    def fake_get(url, params=None, timeout=None):
        if params["league"] == "liga-rota":
            return _make_response(404, text='{"error":"League not found"}')
        return _make_response(200, [_event_json("evt-ok")])

    with patch.object(provider._session, "get", side_effect=fake_get):
        events = provider.list_events(sport="football", leagues=leagues)

    assert {e.event_id for e in events} == {"evt-ok"}


def test_list_events_without_leagues_makes_a_single_call_with_no_league_param():
    """leagues=None (o []) sigue significando 'todas las ligas' -> una sola
    llamada, sin el parámetro 'league' -- comportamiento sin cambios."""
    provider = OddsApiIoProvider(api_key="fake-key")
    ok_resp = _make_response(200, [_event_json("evt-1"), _event_json("evt-2")])

    with patch.object(provider._session, "get", return_value=ok_resp) as mock_get:
        events = provider.list_events(sport="football", leagues=None)

    assert mock_get.call_count == 1
    assert "league" not in mock_get.call_args.kwargs["params"]
    assert {e.event_id for e in events} == {"evt-1", "evt-2"}


def test_get_retries_on_server_error_5xx():
    provider = OddsApiIoProvider(api_key="fake-key")
    server_error = _make_response(500, text="internal error")
    ok_resp = _make_response(200, {"ok": True})

    with patch.object(provider._session, "get", side_effect=[server_error, ok_resp]) as mock_get, \
         patch("valuebet.odds_provider.time.sleep"):
        data = provider._get("/events")

    assert data == {"ok": True}
    assert mock_get.call_count == 2
