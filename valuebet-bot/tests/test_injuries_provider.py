"""ApiFootballProvider: solo se prueba la plomería HTTP (auth, reintentos) —
ver el aviso en injuries_provider.py sobre por qué todavía no hay parseo de
campos específicos que probar."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.injuries_provider import ApiFootballProvider


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
        ApiFootballProvider(api_key="")
        assert False, "se esperaba ValueError"
    except ValueError as exc:
        assert "api_key" in str(exc)


def test_direct_auth_uses_apisports_header():
    provider = ApiFootballProvider(api_key="fake-key", via_rapidapi=False)
    assert provider._session.headers["x-apisports-key"] == "fake-key"
    assert "x-rapidapi-key" not in provider._session.headers


def test_rapidapi_auth_uses_rapidapi_headers():
    provider = ApiFootballProvider(api_key="fake-key", via_rapidapi=True)
    assert provider._session.headers["x-rapidapi-key"] == "fake-key"
    assert provider._session.headers["x-rapidapi-host"] == "api-football-v1.p.rapidapi.com"
    assert "x-apisports-key" not in provider._session.headers


def test_raw_get_returns_json_on_success():
    provider = ApiFootballProvider(api_key="fake-key")
    ok_resp = _make_response(200, {"response": []})
    with patch.object(provider._session, "get", return_value=ok_resp) as mock_get:
        data = provider.raw_get("/teams", {"search": "Real Madrid"})
    assert data == {"response": []}
    assert mock_get.call_count == 1


def test_raw_get_retries_on_429():
    provider = ApiFootballProvider(api_key="fake-key")
    rate_limited = _make_response(429, text="rate limited")
    ok_resp = _make_response(200, {"ok": True})
    with patch.object(provider._session, "get", side_effect=[rate_limited, ok_resp]) as mock_get, \
         patch("valuebet.injuries_provider.time.sleep"):
        data = provider.raw_get("/teams")
    assert data == {"ok": True}
    assert mock_get.call_count == 2


def test_raw_get_does_not_retry_on_400():
    provider = ApiFootballProvider(api_key="fake-key")
    bad_resp = _make_response(400, text='{"error":"bad request"}')
    with patch.object(provider._session, "get", return_value=bad_resp) as mock_get:
        try:
            provider.raw_get("/teams")
            assert False, "se esperaba RuntimeError"
        except RuntimeError:
            pass
    assert mock_get.call_count == 1


# --- get_injuries_for_date / injuries_for_team: forma confirmada contra la
# API real el 2026-08-25 con scripts/verify_api_football.py (ver el aviso
# grande en injuries_provider.py, incluyendo el bloqueo real de
# team+season=temporada-en-curso en el plan gratuito y por qué se usa date). ---

_SAMPLE_INJURIES_RESPONSE = {
    "get": "injuries",
    "parameters": {"date": "2026-08-25"},
    "errors": [],
    "results": 2,
    "response": [
        {
            "player": {"id": 348230, "name": "K. Aliyev", "type": "Missing Fixture", "reason": "Tendon Injury"},
            "team": {"id": 13976, "name": "Sabah FA", "logo": "x"},
            "fixture": {"id": 1622626, "date": "2026-08-25T16:45:00+00:00"},
            "league": {"id": 2, "season": 2026, "name": "UEFA Champions League", "country": "World"},
        },
        {
            "player": {"id": 174827, "name": "S. Solvet", "type": "Questionable", "reason": "Injury"},
            "team": {"id": 13976, "name": "Sabah FA", "logo": "x"},
            "fixture": {"id": 1622626, "date": "2026-08-25T16:45:00+00:00"},
            "league": {"id": 2, "season": 2026, "name": "UEFA Champions League", "country": "World"},
        },
    ],
}


def test_get_injuries_for_date_returns_response_list():
    provider = ApiFootballProvider(api_key="fake-key")
    with patch.object(provider, "raw_get", return_value=_SAMPLE_INJURIES_RESPONSE) as mock_raw_get:
        result = provider.get_injuries_for_date("2026-08-25")
    assert result == _SAMPLE_INJURIES_RESPONSE["response"]
    mock_raw_get.assert_called_once_with("/injuries", {"date": "2026-08-25"})


def test_get_injuries_for_date_returns_empty_on_plan_error():
    """Caso real encontrado (2026-08-25): team+season=temporada-en-curso
    devuelve 200 OK pero con errors.plan — no debe tratarse como datos."""
    provider = ApiFootballProvider(api_key="fake-key")
    plan_error_response = {
        "get": "injuries",
        "parameters": {"team": "541", "season": "2026"},
        "errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."},
        "results": 0,
        "response": [],
    }
    with patch.object(provider, "raw_get", return_value=plan_error_response):
        result = provider.get_injuries_for_date("2026-08-25")
    assert result == []


def test_injuries_for_team_filters_by_name():
    notes = ApiFootballProvider.injuries_for_team(_SAMPLE_INJURIES_RESPONSE["response"], "Sabah FA")
    assert len(notes) == 2
    assert "K. Aliyev (Tendon Injury)" in notes
    assert "S. Solvet (Injury)" in notes


def test_injuries_for_team_returns_empty_for_other_team():
    notes = ApiFootballProvider.injuries_for_team(_SAMPLE_INJURIES_RESPONSE["response"], "Real Madrid")
    assert notes == []
