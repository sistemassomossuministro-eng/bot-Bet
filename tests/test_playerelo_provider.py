"""PlayerEloProvider: solo se prueba la plomería HTTP (auth, reintentos) —
ver el aviso en playerelo_provider.py sobre por qué todavía no hay parseo de
campos específicos que probar."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.playerelo_provider import PlayerEloProvider


def _make_response(status_code, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.headers = {}
    if status_code >= 400:
        import requests

        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.side_effect = None
    return resp


def test_missing_api_key_raises():
    try:
        PlayerEloProvider(api_key="")
        assert False, "se esperaba ValueError"
    except ValueError as exc:
        assert "api_key" in str(exc)


def test_placeholder_api_key_raises():
    try:
        PlayerEloProvider(api_key="TU_API_KEY_AQUI")
        assert False, "se esperaba ValueError"
    except ValueError:
        pass


def test_sends_bearer_auth_header():
    provider = PlayerEloProvider(api_key="fake-key")
    assert provider._session.headers["Authorization"] == "Bearer fake-key"


def test_raw_get_returns_json_on_success():
    provider = PlayerEloProvider(api_key="fake-key")
    ok_resp = _make_response(200, {"hello": "world"})
    with patch.object(provider._session, "get", return_value=ok_resp) as mock_get:
        data = provider.raw_get("/v1/predictions", {"home": "A", "away": "B"})
    assert data == {"hello": "world"}
    assert mock_get.call_count == 1


def test_raw_get_retries_on_429():
    provider = PlayerEloProvider(api_key="fake-key")
    rate_limited = _make_response(429, text="rate limited")
    ok_resp = _make_response(200, {"ok": True})
    with patch.object(provider._session, "get", side_effect=[rate_limited, ok_resp]) as mock_get, \
         patch("valuebet.playerelo_provider.time.sleep"):
        data = provider.raw_get("/v1/predictions")
    assert data == {"ok": True}
    assert mock_get.call_count == 2


def test_raw_get_does_not_retry_on_400():
    provider = PlayerEloProvider(api_key="fake-key")
    bad_resp = _make_response(400, text='{"error":"bad request"}')
    with patch.object(provider._session, "get", return_value=bad_resp) as mock_get:
        try:
            provider.raw_get("/v1/predictions")
            assert False, "se esperaba RuntimeError"
        except RuntimeError:
            pass
    assert mock_get.call_count == 1


# --- get_predictions_for_date / find_prediction: forma confirmada contra la
# API real el 2026-08-25 con scripts/verify_playerelo.py (ver el aviso
# grande en playerelo_provider.py). ---

_SAMPLE_PREDICTIONS = [
    {
        "fixture_id": 1493085,
        "kickoff_time": "2026-08-25T00:15:00+00:00",
        "league_name": "Liga Profesional Argentina",
        "league_id": 128,
        "home_team": "Lanus",
        "away_team": "Argentinos JRS",
        "home_team_elo": 1547.2,
        "away_team_elo": 1612.7,
        "p_home": 0.3872,
        "p_draw": 0.2899,
        "p_away": 0.3229,
        "status": "started",
    },
    {
        "fixture_id": 1601121,
        "kickoff_time": "2026-08-24T12:15:00+00:00",
        "league_name": "Super League",
        "league_id": 278,
        "home_team": "Sabah FA",
        "away_team": "Imigresen",
        "home_team_elo": None,
        "away_team_elo": None,
        "p_home": None,
        "p_draw": None,
        "p_away": None,
        "status": "started",
    },
]


def test_get_predictions_for_date_returns_list():
    provider = PlayerEloProvider(api_key="fake-key")
    with patch.object(provider, "raw_get", return_value=_SAMPLE_PREDICTIONS) as mock_raw_get:
        result = provider.get_predictions_for_date("2026-08-25")
    assert result == _SAMPLE_PREDICTIONS
    mock_raw_get.assert_called_once_with("/v1/predictions", {"date": "2026-08-25"})


def test_get_predictions_for_date_tolerates_non_list_response():
    """Si la API cambiara de forma (ej. un objeto de error), no debe reventar."""
    provider = PlayerEloProvider(api_key="fake-key")
    with patch.object(provider, "raw_get", return_value={"error": "algo raro"}):
        result = provider.get_predictions_for_date("2026-08-25")
    assert result == []


def test_find_prediction_matches_by_team_name():
    match = PlayerEloProvider.find_prediction(_SAMPLE_PREDICTIONS, "Lanus", "Argentinos JRS")
    assert match is not None
    assert match["fixture_id"] == 1493085


def test_find_prediction_returns_none_if_no_match():
    """Equipo emparejado por nombre de forma estricta (ver team_match.py) —
    un nombre distinto no debe emparejar con nada."""
    match = PlayerEloProvider.find_prediction(_SAMPLE_PREDICTIONS, "Real Madrid", "Barcelona")
    assert match is None
