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
