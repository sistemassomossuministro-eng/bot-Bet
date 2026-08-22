import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.descriptions import describe_pick, describe_selection, market_label


def test_h2h_home():
    assert describe_selection("h2h", "home", "Millonarios", "Nacional") == "Gana Millonarios (Local)"


def test_h2h_away():
    assert describe_selection("h2h", "away", "Millonarios", "Nacional") == "Gana Nacional (Visitante)"


def test_h2h_draw():
    assert describe_selection("h2h", "draw", "Millonarios", "Nacional") == "Empate"


def test_totals_over():
    desc = describe_selection("totals", "over_2.5", "Millonarios", "Nacional")
    assert desc == "Más de 2.5 goles en el partido"


def test_totals_under_whole_number():
    desc = describe_selection("totals", "under_3", "Millonarios", "Nacional")
    assert desc == "Menos de 3 goles en el partido"  # %g no debe imprimir '3.0'


def test_spreads_home_negative():
    desc = describe_selection("spreads", "home_-1.5", "Millonarios", "Nacional")
    assert desc == "Millonarios con hándicap -1.5"


def test_spreads_away_positive():
    desc = describe_selection("spreads", "away_1.5", "Millonarios", "Nacional")
    assert desc == "Nacional con hándicap +1.5"


def test_unknown_market_falls_back_gracefully():
    desc = describe_selection("player_props", "anytime_scorer", "Millonarios", "Nacional")
    assert "anytime_scorer" in desc


def test_market_label_known_and_unknown():
    assert market_label("h2h") == "Resultado (1X2)"
    assert market_label("weird_market") == "weird_market"


def test_describe_pick_includes_odds_and_bookmaker():
    line = describe_pick("h2h", "home", "Millonarios", "Nacional", 2.20, "Betplay")
    assert line == "Gana Millonarios (Local) @ 2.20 (Betplay)"
