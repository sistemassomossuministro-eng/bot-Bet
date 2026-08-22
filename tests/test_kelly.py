import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.kelly import BankrollLimits, kelly_full_fraction, suggest_stake


def test_kelly_full_fraction_no_edge():
    # Prob justa 45% pero cuota implica 50% (1/2.0) -> sin ventaja
    f = kelly_full_fraction(0.45, 2.0)
    assert f == 0.0


def test_kelly_full_fraction_with_edge():
    # Prob justa 55%, cuota 2.0 (implica 50%) -> ventaja clara
    f = kelly_full_fraction(0.55, 2.0)
    assert f > 0
    # Kelly clásico: f* = (bp - q) / b, b=1 => f* = 2p - 1 = 0.10
    assert abs(f - 0.10) < 1e-9


def test_suggest_stake_respects_max_stake_pct():
    limits = BankrollLimits(total=1_000_000, kelly_fraction=1.0, max_stake_pct=0.02)
    # Ventaja enorme para forzar que el tope por apuesta sea el límite activo
    suggestion = suggest_stake(fair_probability=0.9, decimal_odds=3.0, limits=limits)
    assert suggestion.stake <= 0.02 * 1_000_000 + 1e-6
    assert suggestion.capped


def test_suggest_stake_respects_daily_loss_limit():
    limits = BankrollLimits(total=1_000_000, daily_loss_limit_pct=0.05)
    suggestion = suggest_stake(
        fair_probability=0.9,
        decimal_odds=3.0,
        limits=limits,
        pnl_today=-60_000,  # ya se perdió más del 5% de 1,000,000
    )
    assert suggestion.stake == 0.0
    assert "diaria" in suggestion.reason.lower() or "límite" in suggestion.reason.lower()


def test_suggest_stake_no_edge_gives_zero():
    limits = BankrollLimits(total=1_000_000)
    suggestion = suggest_stake(fair_probability=0.4, decimal_odds=2.0, limits=limits)
    assert suggestion.stake == 0.0


def test_suggest_stake_respects_daily_stake_budget():
    limits = BankrollLimits(total=1_000_000, kelly_fraction=1.0, max_stake_pct=1.0, daily_stake_limit_pct=0.10)
    suggestion = suggest_stake(
        fair_probability=0.9,
        decimal_odds=3.0,
        limits=limits,
        staked_today=95_000,  # ya casi se agotó el presupuesto diario de 100,000
    )
    assert suggestion.stake <= 5_000 + 1e-6
