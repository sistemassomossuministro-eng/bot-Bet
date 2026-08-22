"""Determina si una selección ganó, perdió o empujó (push) a partir del marcador final.

Soporta los 3 tipos de mercado que produce el sistema (ver `_MARKET_NAME_MAP` en
odds_provider.py y `_parse_odds_line`):

- h2h (1x2 / moneyline): selección en {"home", "draw", "away"}.
- totals (over/under): selección con forma "over_<punto>" / "under_<punto>".
- spreads (hándicap): selección con forma "home_<punto>" / "away_<punto>".

Si el market_key o el nombre de la selección no calzan con ninguno de estos
patrones, se devuelve 'unsupported' en vez de arriesgar un veredicto incorrecto.
"""
from __future__ import annotations

from typing import Optional, Tuple


def parse_point_suffix(selection: str) -> Tuple[str, Optional[float]]:
    """'over_2.5' -> ('over', 2.5); 'home_-1.5' -> ('home', -1.5); 'home' -> ('home', None)."""
    parts = selection.rsplit("_", 1)
    if len(parts) == 2:
        try:
            return parts[0], float(parts[1])
        except ValueError:
            pass
    return selection, None


def settle_selection(
    market_key: str, selection: str, home_score: int, away_score: int
) -> Tuple[str, str]:
    """Devuelve (resultado, detalle) donde resultado in {'won','lost','push','unsupported'}."""

    if market_key == "h2h":
        if selection not in ("home", "draw", "away"):
            return "unsupported", f"selección desconocida para h2h: {selection}"
        if home_score > away_score:
            actual = "home"
        elif away_score > home_score:
            actual = "away"
        else:
            actual = "draw"
        detail = f"marcador {home_score}-{away_score} -> resultado real: {actual}"
        return ("won" if selection == actual else "lost"), detail

    if market_key == "totals":
        side, point = parse_point_suffix(selection)
        if point is None or side not in ("over", "under"):
            return "unsupported", f"no se pudo interpretar la línea de totals: {selection}"
        total = home_score + away_score
        if total == point:
            return "push", f"total {total} == línea {point}"
        went_over = total > point
        won = (side == "over" and went_over) or (side == "under" and not went_over)
        return ("won" if won else "lost"), f"total {total} vs línea {point} ({side})"

    if market_key == "spreads":
        side, point = parse_point_suffix(selection)
        if point is None or side not in ("home", "away"):
            return "unsupported", f"no se pudo interpretar la línea de hándicap: {selection}"
        adjusted_home = home_score + (point if side == "home" else 0)
        adjusted_away = away_score + (point if side == "away" else 0)
        if adjusted_home == adjusted_away:
            return "push", f"hándicap empata {adjusted_home}-{adjusted_away}"
        home_covers = adjusted_home > adjusted_away
        won = (side == "home" and home_covers) or (side == "away" and not home_covers)
        return ("won" if won else "lost"), f"ajustado {adjusted_home:.1f}-{adjusted_away:.1f} ({side} {point:+})"

    return "unsupported", f"market_key no soportado para liquidación automática: {market_key}"
