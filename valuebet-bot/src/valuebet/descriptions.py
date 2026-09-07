"""Traduce los códigos internos (market_key + selección) a texto legible en
español, usando los nombres reales de los equipos — para que un mensaje de
Telegram o una imagen digan "Gana Millonarios (Local) @2.20" en vez de
"h2h · home @2.20", que no dice nada si no conoces la convención interna.

Sigue la misma convención de nombres que `odds_provider.py` (h2h/totals/spreads
y sufijos "_<punto>") y `settlement.py` (que liquida esas mismas selecciones).
"""
from __future__ import annotations

from .settlement import parse_point_suffix

MARKET_LABELS = {
    "h2h": "Resultado (1X2)",
    "totals": "Total de goles",
    "spreads": "Hándicap asiático",
    "btts": "Ambos anotan",
}


def market_label(market_key: str) -> str:
    return MARKET_LABELS.get(market_key, market_key)


def describe_selection(market_key: str, selection: str, home_team: str, away_team: str) -> str:
    """Ej.: ('h2h', 'home', 'Millonarios', 'Nacional') -> 'Gana Millonarios (Local)'
    ('totals', 'over_2.5', ...) -> 'Más de 2.5 goles en el partido'
    ('spreads', 'home_-1.5', 'Millonarios', 'Nacional') -> 'Millonarios con hándicap -1.5'
    Si no reconoce el patrón, devuelve algo razonable en vez de fallar."""

    if market_key == "h2h":
        if selection == "home":
            return f"Gana {home_team} (Local)"
        if selection == "away":
            return f"Gana {away_team} (Visitante)"
        if selection == "draw":
            return "Empate"
        return f"{market_label(market_key)}: {selection}"

    if market_key == "totals":
        side, point = parse_point_suffix(selection)
        if point is not None and side in ("over", "under"):
            word = "Más de" if side == "over" else "Menos de"
            return f"{word} {point:g} goles en el partido"
        return f"{market_label(market_key)}: {selection}"

    if market_key == "spreads":
        side, point = parse_point_suffix(selection)
        if point is not None and side in ("home", "away"):
            team = home_team if side == "home" else away_team
            return f"{team} con hándicap {point:+.1f}"
        return f"{market_label(market_key)}: {selection}"

    if market_key == "btts":
        if selection == "yes":
            return "Ambos equipos anotan"
        if selection == "no":
            return "No anotan ambos equipos"
        return f"{market_label(market_key)}: {selection}"

    return f"{market_key}: {selection}"


def describe_pick(market_key: str, selection: str, home_team: str, away_team: str, odds: float, bookmaker: str) -> str:
    """Descripción completa de una sola línea, lista para mostrar: incluye qué
    se apuesta, en qué mercado, a qué cuota y en qué casa."""
    return f"{describe_selection(market_key, selection, home_team, away_team)} @ {odds:.2f} ({bookmaker})"
