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

from valuebet.odds_provider import OddsApiIoProvider, _parse_odds_line


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


def test_parse_odds_line_totals_without_point_is_discarded():
    """Bug real de producción: una línea de 'totals' sin 'point' colapsaría
    distintas líneas de gol (0.5, 2.5, 8.5, ...) al mismo nombre de resultado
    ('over'/'under') y terminaría comparando cuotas de líneas totalmente
    distintas entre sí. Se descarta la línea entera en vez de arriesgar eso."""
    line = {"over": "1.90", "under": "1.90"}  # sin 'point'
    assert _parse_odds_line(line, market_key="totals") == []
    assert _parse_odds_line(line, market_key="spreads") == []


def test_parse_odds_line_totals_with_point_still_works():
    line = {"over": "1.90", "under": "1.90", "point": 2.5}
    outcomes = _parse_odds_line(line, market_key="totals")
    names = {o.name for o in outcomes}
    assert names == {"over_2.5", "under_2.5"}


def test_parse_odds_line_h2h_without_point_is_unaffected():
    # h2h (home/draw/away) nunca lleva 'point' — no debe descartarse por eso.
    line = {"home": "1.95", "draw": "3.60", "away": "4.20"}
    outcomes = _parse_odds_line(line, market_key="h2h")
    names = {o.name for o in outcomes}
    assert names == {"home", "draw", "away"}


def test_get_retries_on_server_error_5xx():
    provider = OddsApiIoProvider(api_key="fake-key")
    server_error = _make_response(500, text="internal error")
    ok_resp = _make_response(200, {"ok": True})

    with patch.object(provider._session, "get", side_effect=[server_error, ok_resp]) as mock_get, \
         patch("valuebet.odds_provider.time.sleep"):
        data = provider._get("/events")

    assert data == {"ok": True}
    assert mock_get.call_count == 2
