"""Cálculo de probabilidad 'justa' (sin margen de casa) y valor esperado (EV).

Toda casa de apuestas incluye un margen (overround / vig) en sus cuotas: la suma
de las probabilidades implícitas de todos los resultados de un mercado suma
más de 100%. Para estimar la probabilidad "real" de cada resultado hay que
quitar ese margen (devig) usando las cuotas de un libro de referencia
(idealmente "sharp", ej. Pinnacle; si no está disponible, el más líquido/
eficiente al que se tenga acceso, ej. Bet365 — ver config.example.yaml) que
se asume eficiente/bien calibrado.

Referencias de los métodos:
- Multiplicative (o "basic"): normaliza dividiendo cada probabilidad implícita
  por la suma total. Simple y el más usado en la práctica.
- Shin: modelo que asume una fracción de "insiders" y corrige de forma no
  lineal; suele dar estimaciones algo más precisas en mercados con favoritos
  muy marcados, a costa de mayor complejidad.
"""
from __future__ import annotations

from typing import List

import math


def implied_probabilities(decimal_odds: List[float]) -> List[float]:
    """Probabilidad implícita bruta (con margen) de cada cuota decimal."""
    return [1.0 / o for o in decimal_odds]


def multiplicative_devig(decimal_odds: List[float]) -> List[float]:
    """Quita el margen normalizando las probabilidades implícitas para que sumen 1."""
    if not decimal_odds:
        return []
    implied = implied_probabilities(decimal_odds)
    total = sum(implied)
    if total <= 0:
        raise ValueError("Suma de probabilidades implícitas inválida (<= 0)")
    return [p / total for p in implied]


def shin_devig(decimal_odds: List[float], tolerance: float = 1e-10, max_iter: int = 100) -> List[float]:
    """Devig usando el modelo de Shin (1992/1993).

    Resuelve para z (fracción de apostadores informados) tal que las
    probabilidades ajustadas sumen 1, usando bisección sobre z en [0, 0.5).
    """
    if not decimal_odds:
        return []
    implied = implied_probabilities(decimal_odds)
    total = sum(implied)
    if total <= 1.0:
        # No hay margen medible; cae al método multiplicativo.
        return multiplicative_devig(decimal_odds)

    def probs_for_z(z: float) -> List[float]:
        # Fórmula de Shin: p_i = (sqrt(z^2 + 4(1-z) * pi_i^2 / total) - z) / (2(1-z))
        out = []
        for pi in implied:
            inner = z * z + 4 * (1 - z) * (pi * pi) / total
            out.append((math.sqrt(inner) - z) / (2 * (1 - z)))
        return out

    lo, hi = 0.0, 0.499999
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        s = sum(probs_for_z(mid))
        if abs(s - 1.0) < tolerance:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    fair = probs_for_z(mid)
    # Corrección de redondeo final para que sumen exactamente 1.
    total_fair = sum(fair)
    return [p / total_fair for p in fair]


DEVIG_METHODS = {
    "multiplicative": multiplicative_devig,
    "shin": shin_devig,
}


def fair_probabilities(decimal_odds: List[float], method: str = "multiplicative") -> List[float]:
    fn = DEVIG_METHODS.get(method)
    if fn is None:
        raise ValueError(f"Método de devig desconocido: {method}. Usa uno de {list(DEVIG_METHODS)}")
    return fn(decimal_odds)


def expected_value_pct(offered_decimal_odds: float, fair_probability: float) -> float:
    """EV% = (probabilidad justa * cuota ofrecida - 1) * 100

    Un EV positivo significa que, en promedio y a largo plazo, la cuota ofrecida
    paga más de lo que "debería" según la probabilidad justa estimada.
    No garantiza ganar esa apuesta individual — es una ventaja estadística.
    """
    if offered_decimal_odds <= 1.0:
        raise ValueError("La cuota decimal debe ser > 1.0")
    if not (0.0 < fair_probability < 1.0):
        raise ValueError("La probabilidad justa debe estar entre 0 y 1")
    return (fair_probability * offered_decimal_odds - 1.0) * 100.0
