"""Gestión de banca: criterio de Kelly (fraccional) y límites de seguridad.

El criterio de Kelly da la fracción óptima de la banca a apostar para
maximizar el crecimiento geométrico esperado, PERO asume que la probabilidad
estimada es exacta — algo que nunca es cierto al 100% en la práctica. Por eso
este módulo siempre aplica un "Kelly fraccional" (una fracción del Kelly
completo, ej. 25%) y además impone topes duros configurables por el usuario,
incluyendo límites diarios pensados para evitar pérdidas descontroladas
(apuesta responsable).
"""
from __future__ import annotations

from dataclasses import dataclass


def kelly_full_fraction(fair_probability: float, decimal_odds: float) -> float:
    """Fracción de Kelly completo (0 si no hay ventaja, puede ser negativa si EV<0)."""
    b = decimal_odds - 1.0  # ganancia neta por unidad apostada
    if b <= 0:
        return 0.0
    q = 1.0 - fair_probability
    f = (b * fair_probability - q) / b
    return max(f, 0.0)


@dataclass
class BankrollLimits:
    total: float
    kelly_fraction: float = 0.25       # fracción del Kelly completo a usar
    max_stake_pct: float = 0.02        # tope por apuesta individual
    daily_stake_limit_pct: float = 0.10
    daily_loss_limit_pct: float = 0.05


@dataclass
class StakeSuggestion:
    stake: float
    kelly_fraction_used: float
    capped: bool
    reason: str = ""


def suggest_stake(
    fair_probability: float,
    decimal_odds: float,
    limits: BankrollLimits,
    staked_today: float = 0.0,
    pnl_today: float = 0.0,
) -> StakeSuggestion:
    """Calcula el stake sugerido aplicando Kelly fraccional y los topes configurados.

    Devuelve stake=0 si el límite de pérdida diaria ya se alcanzó o si no hay
    ventaja (EV<=0 implícito en full_kelly<=0).
    """
    if pnl_today <= -abs(limits.daily_loss_limit_pct) * limits.total:
        return StakeSuggestion(
            stake=0.0,
            kelly_fraction_used=0.0,
            capped=True,
            reason="Límite de pérdida diaria alcanzado. No se sugieren más apuestas hoy.",
        )

    full_kelly = kelly_full_fraction(fair_probability, decimal_odds)
    if full_kelly <= 0:
        return StakeSuggestion(stake=0.0, kelly_fraction_used=0.0, capped=False, reason="Sin ventaja (EV<=0).")

    fractional = full_kelly * limits.kelly_fraction
    stake = fractional * limits.total

    capped = False
    reason = ""

    max_stake = limits.max_stake_pct * limits.total
    if stake > max_stake:
        stake = max_stake
        capped = True
        reason = "Tope por apuesta individual aplicado."

    remaining_daily_budget = max(limits.daily_stake_limit_pct * limits.total - staked_today, 0.0)
    if stake > remaining_daily_budget:
        stake = remaining_daily_budget
        capped = True
        reason = "Tope de exposición diaria aplicado."

    return StakeSuggestion(
        stake=round(stake, 2),
        kelly_fraction_used=round(fractional, 6),
        capped=capped,
        reason=reason,
    )
