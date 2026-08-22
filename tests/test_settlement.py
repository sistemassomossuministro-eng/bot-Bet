import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.settlement import settle_selection


def test_h2h_home_win():
    result, _ = settle_selection("h2h", "home", 2, 1)
    assert result == "won"


def test_h2h_home_selection_but_away_won():
    result, _ = settle_selection("h2h", "home", 0, 1)
    assert result == "lost"


def test_h2h_draw():
    result, _ = settle_selection("h2h", "draw", 1, 1)
    assert result == "won"
    result, _ = settle_selection("h2h", "home", 1, 1)
    assert result == "lost"


def test_h2h_unknown_selection():
    result, _ = settle_selection("h2h", "over", 1, 1)
    assert result == "unsupported"


def test_totals_over_wins():
    result, _ = settle_selection("totals", "over_2.5", 2, 1)  # total 3 > 2.5
    assert result == "won"


def test_totals_under_wins():
    result, _ = settle_selection("totals", "under_2.5", 1, 0)  # total 1 < 2.5
    assert result == "won"


def test_totals_push_on_whole_number_line():
    result, _ = settle_selection("totals", "over_2", 1, 1)  # total 2 == línea 2
    assert result == "push"


def test_totals_malformed_selection():
    result, _ = settle_selection("totals", "over", 2, 1)
    assert result == "unsupported"


def test_spreads_home_covers():
    # Hándicap -1.5 para el local: local gana 3-1 -> ajustado 1.5-1 -> cubre
    result, _ = settle_selection("spreads", "home_-1.5", 3, 1)
    assert result == "won"


def test_spreads_home_does_not_cover():
    # Local gana 1-0 pero con -1.5 el ajustado es -0.5-0 -> no cubre
    result, _ = settle_selection("spreads", "home_-1.5", 1, 0)
    assert result == "lost"


def test_spreads_push():
    result, _ = settle_selection("spreads", "home_-1.0", 2, 1)  # ajustado 1-1
    assert result == "push"


def test_unsupported_market():
    result, _ = settle_selection("player_props", "anytime_scorer", 1, 1)
    assert result == "unsupported"
