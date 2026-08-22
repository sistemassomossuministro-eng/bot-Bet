"""Cliente de Instagram — mockeado, sin red real."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.instagram import InstagramPublisher


def _resp(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    return r


def test_publish_image_success_two_step_flow():
    publisher = InstagramPublisher(access_token="tok", ig_user_id="123")

    create_resp = _resp(200, {"id": "container-1"})
    status_resp = _resp(200, {"status_code": "FINISHED"})
    publish_resp = _resp(200, {"id": "media-1"})

    with patch("valuebet.instagram.requests.post", side_effect=[create_resp, publish_resp]) as mock_post, \
         patch("valuebet.instagram.requests.get", return_value=status_resp) as mock_get:
        media_id = publisher.publish_image("https://example.com/img.jpg", caption="hola")

    assert media_id == "media-1"
    assert mock_post.call_count == 2
    assert mock_get.call_count == 1

    # Primer POST crea el container con image_url + caption.
    first_call_kwargs = mock_post.call_args_list[0].kwargs
    assert first_call_kwargs["data"]["image_url"] == "https://example.com/img.jpg"
    assert first_call_kwargs["data"]["caption"] == "hola"

    # Segundo POST publica con el creation_id devuelto por el primero.
    second_call_kwargs = mock_post.call_args_list[1].kwargs
    assert second_call_kwargs["data"]["creation_id"] == "container-1"


def test_publish_image_returns_none_when_container_creation_fails():
    publisher = InstagramPublisher(access_token="tok", ig_user_id="123")
    create_resp = _resp(400, {"error": {"message": "token inválido"}})

    with patch("valuebet.instagram.requests.post", return_value=create_resp):
        media_id = publisher.publish_image("https://example.com/img.jpg")

    assert media_id is None


def test_publish_image_returns_none_when_publish_step_fails():
    publisher = InstagramPublisher(access_token="tok", ig_user_id="123")
    create_resp = _resp(200, {"id": "container-1"})
    status_resp = _resp(200, {"status_code": "FINISHED"})
    publish_resp = _resp(500, {"error": {"message": "boom"}})

    with patch("valuebet.instagram.requests.post", side_effect=[create_resp, publish_resp]), \
         patch("valuebet.instagram.requests.get", return_value=status_resp):
        media_id = publisher.publish_image("https://example.com/img.jpg")

    assert media_id is None


def test_publish_image_never_raises_on_network_exception():
    publisher = InstagramPublisher(access_token="tok", ig_user_id="123")
    with patch("valuebet.instagram.requests.post", side_effect=ConnectionError("sin red")):
        media_id = publisher.publish_image("https://example.com/img.jpg")

    assert media_id is None
