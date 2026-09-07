import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.social_image import (
    BADGE_LOST,
    BADGE_WON,
    PickRow,
    StatTile,
    render_monthly_summary_image,
    render_picks_image,
    render_results_image,
    result_badge,
)


def test_render_picks_image_creates_square_png():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "picks.png")
        rows = [PickRow(f"Equipo A{i} vs Equipo B{i}", f"h2h · home @2.1{i}", f"+{i}.0% EV") for i in range(10)]
        path = render_picks_image("2026-08-22", rows, out)
        from PIL import Image

        img = Image.open(path)
        assert img.size == (1080, 1080)


def test_render_picks_image_handles_empty_list():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "empty.png")
        path = render_picks_image("2026-08-22", [], out)
        from PIL import Image

        assert Image.open(path).size == (1080, 1080)


def test_render_results_image_creates_square_png():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "results.png")
        color, label = result_badge("won")
        rows = [PickRow("Equipo A vs Equipo B", "h2h · home @2.10 (2-1)", label, color)]
        path = render_results_image("2026-08-21", rows, "1/1 aciertos", out)
        from PIL import Image

        assert Image.open(path).size == (1080, 1080)


def test_result_badge_known_and_unknown():
    color, label = result_badge("won")
    assert label == "GANADA"
    color2, label2 = result_badge("something_weird")
    assert label2 == "SOMETHING_WEIRD"


def test_render_monthly_summary_image_profitable():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "monthly.png")
        tiles = [
            StatTile("Total de picks", "287"),
            StatTile("Ganados", "132", value_color=BADGE_WON),
            StatTile("Perdidos", "118", value_color=BADGE_LOST),
            StatTile("Tasa de acierto", "52.8%"),
            StatTile("Profit (stake plano)", "+33.8u", value_color=BADGE_WON),
            StatTile("ROI del mes", "13.5%", value_color=BADGE_WON),
        ]
        path = render_monthly_summary_image("Julio 2026", tiles, True, out)
        from PIL import Image

        assert Image.open(path).size == (1080, 1080)


def test_render_monthly_summary_image_not_profitable_and_long_footer_does_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "monthly_loss.png")
        tiles = [StatTile("Total de picks", "10"), StatTile("Profit (stake plano)", "-4.5u", value_color=BADGE_LOST)]
        path = render_monthly_summary_image("Enero 2026", tiles, False, out)
        from PIL import Image

        assert Image.open(path).size == (1080, 1080)
