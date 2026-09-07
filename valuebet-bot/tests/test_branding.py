import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.branding import BRAND_NAME, BRAND_TAGLINE, render_icon, render_profile_icon


def test_render_icon_returns_square_rgba_image():
    icon = render_icon(128)
    assert icon.size == (128, 128)
    assert icon.mode == "RGBA"


def test_render_icon_has_transparent_corners():
    # Las esquinas de la insignia redondeada deben quedar transparentes;
    # el centro (donde va la "B") no.
    icon = render_icon(200)
    r, g, b, a_corner = icon.getpixel((1, 1))
    assert a_corner == 0
    _, _, _, a_center = icon.getpixel((100, 100))
    assert a_center == 255


def test_render_profile_icon_saves_opaque_square_png():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "profile.png")
        path = render_profile_icon(out, size=256)
        from PIL import Image

        img = Image.open(path)
        assert img.size == (256, 256)
        assert img.mode == "RGB"


def test_brand_identity_constants():
    assert BRAND_NAME == "BotBet"
    assert "BOT" not in BRAND_TAGLINE  # el tagline describe el producto, no repite el nombre
