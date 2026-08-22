import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.social_image import PickRow, render_picks_image
from valuebet.social_publish import queue_instagram_image, read_manifest, to_jpeg, write_manifest


def test_to_jpeg_converts_png_to_valid_jpeg():
    with tempfile.TemporaryDirectory() as tmp:
        png_path = str(Path(tmp) / "picks.png")
        render_picks_image("2026-08-22", [PickRow("A vs B", "h2h · home @2.10", "+5.0% EV")], png_path)

        jpg_path = to_jpeg(png_path)
        assert jpg_path.endswith(".jpg")
        from PIL import Image

        img = Image.open(jpg_path)
        assert img.format == "JPEG"
        assert img.size == (1080, 1080)


class _IgConfig:
    def __init__(self, enabled=True):
        self.enabled = enabled


class _Cfg:
    def __init__(self, instagram=None):
        self.instagram = instagram


def test_queue_instagram_image_noop_without_queue():
    # Sin lista de cola (None), no debe hacer nada ni lanzar excepción.
    queue_instagram_image(_Cfg(instagram=_IgConfig(True)), None, "picks", "no_existe.png", "caption")


def test_queue_instagram_image_noop_when_disabled():
    queue = []
    queue_instagram_image(_Cfg(instagram=_IgConfig(False)), queue, "picks", "no_existe.png", "caption")
    queue_instagram_image(_Cfg(instagram=None), queue, "picks", "no_existe.png", "caption")
    assert queue == []


def test_queue_instagram_image_appends_jpeg_entry_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        png_path = str(Path(tmp) / "picks.png")
        render_picks_image("2026-08-22", [], png_path)

        queue = []
        queue_instagram_image(_Cfg(instagram=_IgConfig(True)), queue, "picks", png_path, "hola")

        assert len(queue) == 1
        assert queue[0]["kind"] == "picks"
        assert queue[0]["caption"] == "hola"
        assert queue[0]["path"].endswith(".jpg")
        assert Path(queue[0]["path"]).exists()


def test_write_and_read_manifest_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        entries = [{"kind": "picks", "path": "output/latest_picks.jpg", "caption": "hola"}]
        write_manifest(tmp, entries)

        loaded = read_manifest(tmp)
        assert loaded == entries

        raw = json.loads((Path(tmp) / "instagram_queue.json").read_text())
        assert raw == entries


def test_read_manifest_missing_file_returns_empty_list():
    with tempfile.TemporaryDirectory() as tmp:
        assert read_manifest(tmp) == []
