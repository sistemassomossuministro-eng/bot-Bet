"""Modelos de datos internos, independientes del proveedor de cuotas.

Cualquier proveedor de datos (odds-api.io, the-odds-api.com, un scraper propio, etc.)
debe normalizar su respuesta a estas estructuras. Así el resto del sistema
(devig, EV, Kelly, alertas, storage) no depende de la forma particular de una API externa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Outcome:
    """Un resultado posible de un mercado (ej. 'home', 'draw', 'away') con su cuota decimal."""
    name: str
    price_decimal: float


@dataclass
class BookmakerMarket:
    """Las cuotas que un bookmaker específico ofrece para un mercado de un evento."""
    bookmaker: str
    market_key: str          # ej. "h2h" (1x2 / moneyline), "totals", "spreads"
    updated_at: Optional[datetime]
    outcomes: List[Outcome] = field(default_factory=list)

    def outcome_price(self, name: str) -> Optional[float]:
        for o in self.outcomes:
            if o.name == name:
                return o.price_decimal
        return None


@dataclass
class Event:
    """Un evento deportivo con las cuotas de uno o más bookmakers para uno o más mercados."""
    event_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    commence_time: datetime
    status: str = "pending"
    # bookmaker -> lista de mercados que ofrece
    bookmakers: Dict[str, List[BookmakerMarket]] = field(default_factory=dict)

    def markets_for(self, bookmaker: str, market_key: str) -> List[BookmakerMarket]:
        return [m for m in self.bookmakers.get(bookmaker, []) if m.market_key == market_key]

    def label(self) -> str:
        return f"{self.home_team} vs {self.away_team} ({self.league})"


@dataclass
class ValueBet:
    """Una oportunidad de apuesta de valor detectada por el motor de análisis."""
    event: Event
    market_key: str
    selection: str            # nombre del resultado (ej. "home", "draw", "Over 2.5")
    bookmaker: str             # casa donde está la cuota atractiva (ej. "Betplay")
    offered_odds: float
    fair_probability: float    # probabilidad "justa" estimada tras quitar el margen del libro de referencia
    ev_pct: float               # valor esperado en porcentaje
    reference_bookmakers: List[str]
    suggested_stake: Optional[float] = None
    kelly_fraction_used: Optional[float] = None

    def description(self) -> str:
        """Texto legible de qué se está apostando, ej. 'Gana Millonarios (Local)'
        en vez del código interno 'h2h · home'."""
        from .descriptions import describe_selection  # import local: evita import circular a nivel de módulo

        return describe_selection(self.market_key, self.selection, self.event.home_team, self.event.away_team)

    def summary(self) -> str:
        return (
            f"{self.event.label()}\n"
            f"Apuesta: {self.description()} ({self.market_key} · {self.selection})\n"
            f"Casa: {self.bookmaker} @ {self.offered_odds:.2f}\n"
            f"Prob. justa estimada: {self.fair_probability*100:.1f}% | EV: {self.ev_pct:.2f}%\n"
            f"Referencia: {', '.join(self.reference_bookmakers)}"
        )
