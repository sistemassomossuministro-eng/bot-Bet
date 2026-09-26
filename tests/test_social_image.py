import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.social_image import (
    BADGE_LOST,
    BADGE_WON,
    WIDTH,
    PickRow,
    StatTile,
    render_daily_dashboard_image,
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


def _dashboard_tiles():
    return [
        StatTile("Total de picks", "34"),
        StatTile("Ganados", "19", value_color=BADGE_WON),
        StatTile("Perdidos", "12", value_color=BADGE_LOST),
        StatTile("Pendientes", "3"),
        StatTile("Tasa de acierto", "61.3%"),
        StatTile("Profit (stake plano)", "+6.4u", value_color=BADGE_WON),
        StatTile("ROI del mes", "20.6%", value_color=BADGE_WON),
    ]


def test_render_daily_dashboard_image_profitable_with_chart():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "dashboard.png")
        series = [-0.8, -0.3, 0.9, 2.4, 6.4]
        path = render_daily_dashboard_image(
            "Septiembre 2026", "Corte: 26 de sep. · día 26/30", _dashboard_tiles(), "profitable", series, out
        )
        from PIL import Image

        img = Image.open(path)
        assert img.width == WIDTH
        assert img.height > 1000  # más alto que las piezas cuadradas, por el KPI grid + gráfico


def test_render_daily_dashboard_image_neutral_status_with_empty_series_does_not_crash():
    """Principios de mes: 0 picks decididos todavía y una serie de un solo
    punto (o vacía) no debe romper el dibujo del gráfico (ver
    _draw_cumulative_chart, que solo traza línea con >= 2 puntos)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "dashboard_empty.png")
        tiles = [StatTile("Total de picks", "0"), StatTile("Pendientes", "0")]
        path = render_daily_dashboard_image(
            "Septiembre 2026", "Corte: 1 de sep. · día 1/30", tiles, "neutral", [0.0], out
        )
        from PIL import Image

        assert Image.open(path).width == WIDTH


def test_render_daily_dashboard_image_negative_status():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "dashboard_neg.png")
        path = render_daily_dashboard_image(
            "Enero 2026", "Corte: 10 de ene. · día 10/31", _dashboard_tiles(), "negative", [-1.0, -2.5], out
        )
        from PIL import Image

        assert Image.open(path).width == WIDTH
