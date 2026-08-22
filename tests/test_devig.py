import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.devig import (
    expected_value_pct,
    fair_probabilities,
    implied_probabilities,
    multiplicative_devig,
    shin_devig,
)


def test_implied_probabilities():
    probs = implied_probabilities([2.0, 2.0])
    assert probs == [0.5, 0.5]


def test_multiplicative_devig_sums_to_one():
    odds = [1.90, 3.60, 4.20]  # típico 1x2 con margen
    fair = multiplicative_devig(odds)
    assert math.isclose(sum(fair), 1.0, rel_tol=1e-9)
    assert all(0 < p < 1 for p in fair)


def test_multiplicative_devig_no_margin_case():
    # Cuotas "justas" perfectas (sin margen): 2.0 y 2.0 -> 50/50
    fair = multiplicative_devig([2.0, 2.0])
    assert math.isclose(fair[0], 0.5, rel_tol=1e-9)
    assert math.isclose(fair[1], 0.5, rel_tol=1e-9)


def test_shin_devig_sums_to_one():
    odds = [1.50, 4.50, 6.00]
    fair = shin_devig(odds)
    assert math.isclose(sum(fair), 1.0, rel_tol=1e-6)
    assert all(0 < p < 1 for p in fair)


def test_shin_close_to_multiplicative_for_small_margins():
    odds = [2.00, 2.05]
    mult = multiplicative_devig(odds)
    shin = shin_devig(odds)
    for a, b in zip(mult, shin):
        assert math.isclose(a, b, abs_tol=0.02)


def test_fair_probabilities_unknown_method_raises():
    try:
        fair_probabilities([2.0, 2.0], method="not_a_method")
        assert False, "debería haber lanzado ValueError"
    except ValueError:
        pass


def test_expected_value_positive_case():
    # Prob justa 50%, cuota ofrecida 2.20 (mejor que la cuota justa de 2.00) -> EV positivo
    ev = expected_value_pct(2.20, 0.5)
    assert math.isclose(ev, 10.0, rel_tol=1e-9)


def test_expected_value_negative_case():
    ev = expected_value_pct(1.80, 0.5)
    assert ev < 0


def test_expected_value_invalid_odds_raises():
    try:
        expected_value_pct(1.0, 0.5)
        assert False
    except ValueError:
        pass
