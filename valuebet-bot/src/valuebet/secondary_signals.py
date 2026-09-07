"""Enriquecimiento de los picks del día con señales secundarias externas
(PlayerElo + lesiones de API-Football) — ver el README, "Señales
secundarias", para el contexto completo.

Reglas de diseño, todas deliberadas:
- Solo se llama para los picks que YA pasaron el filtro de EV (~10/día),
  nunca para los cientos de partidos candidatos evaluados cada corrida —
  así caben ambas APIs en sus planes gratuitos (PlayerElo: 500/mes,
  10/min; API-Football: 100/día).
- Se cachea por FECHA dentro de una misma corrida: si varios picks del día
  caen en la misma fecha (lo normal), cada API se consulta una sola vez
  para ese día, no una vez por pick.
- Es puramente informativo — NUNCA toca `ev_pct`/`fair_probability`, que
  siguen calculándose solo a partir del libro de referencia (ver
  value_finder.py). Un fallo de cualquiera de las dos APIs (rate limit,
  timeout, respuesta rara) nunca debe tumbar el job diario — se captura y
  se sigue sin esa señal para ese pick.
- El emparejamiento de equipos es por NOMBRE (ninguna de las dos APIs
  comparte ID de equipo con odds-api.io) y es ESTRICTO a propósito — ver
  team_match.py. Vas a ver picks sin ninguna señal secundaria mientras el
  matching no cubra todas las variantes de nombre reales; es preferible a
  mostrar el dato de un equipo/partido equivocado.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .config import AppConfig
from .models import ValueBet

logger = logging.getLogger(__name__)

_PLAYERELO_FIELD_BY_SELECTION = {"home": "p_home", "draw": "p_draw", "away": "p_away"}


def _playerelo_note(vb: ValueBet, prediction: dict) -> Optional[str]:
    field = _PLAYERELO_FIELD_BY_SELECTION.get(vb.selection)
    if field is None:
        # Solo aplica a h2h (home/draw/away) — PlayerElo no da probabilidad de
        # totals/btts en esta fase (ver el aviso en playerelo_provider.py
        # sobre scoreline_distribution, capturado pero no usado todavía).
        return None
    p = prediction.get(field)
    if p is None:
        return None
    ref = ", ".join(vb.reference_bookmakers) if vb.reference_bookmakers else "el libro de referencia"
    return f"PlayerElo: {p * 100:.1f}% (vs. {vb.fair_probability * 100:.1f}% de {ref})"


def enrich_picks_with_secondary_signals(
    picks: List[ValueBet],
    cfg: AppConfig,
    playerelo_provider=None,
    injuries_provider=None,
) -> List[ValueBet]:
    """Modifica `picks` en el lugar (agrega playerelo_note/injury_notes) y los
    devuelve. `playerelo_provider`/`injuries_provider` son opcionales — si
    alguno es None (o su `secondary_signals.*.enabled` es False), esa señal
    simplemente no se agrega, sin error."""
    if not picks:
        return picks

    ss_cfg = cfg.secondary_signals
    use_playerelo = bool(ss_cfg.playerelo.enabled and playerelo_provider is not None)
    use_injuries = bool(ss_cfg.injuries.enabled and injuries_provider is not None)
    if not use_playerelo and not use_injuries:
        return picks

    predictions_by_date: dict = {}
    injuries_by_date: dict = {}

    for vb in picks:
        date_str = vb.event.commence_time.date().isoformat()

        if use_playerelo and vb.market_key == "h2h":
            if date_str not in predictions_by_date:
                try:
                    predictions_by_date[date_str] = playerelo_provider.get_predictions_for_date(date_str)
                except Exception:
                    logger.exception("PlayerElo: fallo consultando predicciones del %s — se omite esta señal ese día.", date_str)
                    predictions_by_date[date_str] = []
            prediction = playerelo_provider.find_prediction(
                predictions_by_date[date_str], vb.event.home_team, vb.event.away_team
            )
            if prediction is not None:
                vb.playerelo_note = _playerelo_note(vb, prediction)

        if use_injuries:
            if date_str not in injuries_by_date:
                try:
                    injuries_by_date[date_str] = injuries_provider.get_injuries_for_date(date_str)
                except Exception:
                    logger.exception("API-Football: fallo consultando lesiones del %s — se omite esta señal ese día.", date_str)
                    injuries_by_date[date_str] = []
            home_notes = injuries_provider.injuries_for_team(injuries_by_date[date_str], vb.event.home_team)
            away_notes = injuries_provider.injuries_for_team(injuries_by_date[date_str], vb.event.away_team)
            notes = []
            if home_notes:
                notes.append(f"{vb.event.home_team}: " + ", ".join(home_notes))
            if away_notes:
                notes.append(f"{vb.event.away_team}: " + ", ".join(away_notes))
            if notes:
                vb.injury_notes = notes

    return picks
