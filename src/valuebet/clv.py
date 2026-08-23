"""Captura de "cuota de cierre" (closing line) para medir Closing Line Value (CLV).

odds-api.io no ofrece cuotas históricas en el plan gratuito (GET
/historical/closing-lines es de pago) — no se puede reconstruir el precio de
cierre de un partido después de que ya ocurrió. Por eso se captura de forma
activa, en vivo, poco antes de que arranque cada partido: un workflow aparte
y más frecuente que el resumen diario (ver .github/workflows/clv_snapshot.yml)
revisa qué picks pendientes tienen su partido dentro de las próximas
`daily.clv_window_hours` horas, y guarda la cuota que el mismo bookmaker
ofrece en ese momento para la misma selección.

CLV no mide si un pick individual ganó o perdió — eso lo hace settlement.py.
Mide si la cuota que tomaste era mejor que el precio del mercado justo antes
del partido:

    clv_pct = (cuota_tomada / cuota_de_cierre - 1) * 100

Positivo significa que conseguiste una cuota más alta (mejor) que la que
había al cierre — el mercado se movió después a tu favor, evidencia de que
detectaste valor real antes que el resto. Es la métrica que usan los
apostadores profesionales para validar su ventaja, porque se puede leer
pick por pick (a diferencia del acierto/fallo, que necesita miles de
apuestas para decir algo con significancia estadística) — ver el README,
sección "Closing Line Value (CLV)".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .config import AppConfig
from .odds_provider import OddsProvider
from .storage.db import Storage

logger = logging.getLogger(__name__)


def clv_pct(offered_odds: float, closing_odds: float) -> float:
    """% de ventaja de la cuota tomada sobre la cuota de cierre.
    Positivo = mejor precio que el cierre (buena señal de valor real);
    negativo = el mercado se movió en contra del pick."""
    return (offered_odds / closing_odds - 1) * 100


def capture_closing_snapshots(
    cfg: AppConfig,
    provider: OddsProvider,
    storage: Storage,
    now: Optional[datetime] = None,
    window_hours: Optional[float] = None,
) -> int:
    """Para picks pendientes cuyo partido arranca dentro de `window_hours`,
    intenta capturar la cuota actual del mismo bookmaker+mercado+selección
    como aproximación a la cuota de cierre. Devuelve cuántos picks se
    capturaron en esta corrida.

    Pensado para correr con más frecuencia que el resumen diario — cada
    corrida solo mira los partidos que están por arrancar pronto (normalmente
    muy pocos), así que consume poca cuota de la API de cuotas.
    """
    now = now or datetime.utcnow()
    window_hours = cfg.daily.clv_window_hours if window_hours is None else window_hours
    deadline = (now + timedelta(hours=window_hours)).isoformat()

    rows = storage.list_picks_needing_closing_snapshot(deadline)
    if not rows:
        return 0

    by_event: dict = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)

    captured = 0
    for event_id, picks in by_event.items():
        bookmakers = sorted({p["bookmaker"] for p in picks})
        try:
            events = provider.get_events_odds([event_id], bookmakers)
        except Exception:
            logger.exception("Fallo al pedir la cuota de cierre para el evento %s", event_id)
            continue
        if not events:
            logger.warning(
                "El evento %s ya no devolvió datos al pedir su cuota de cierre "
                "(puede que el proveedor ya lo haya retirado)",
                event_id,
            )
            continue
        event = events[0]

        for pick in picks:
            markets = event.markets_for(pick["bookmaker"], pick["market_key"])
            price = None
            for m in markets:
                price = m.outcome_price(pick["selection"])
                if price is not None:
                    break
            if price is None:
                logger.warning(
                    "No se encontró cuota de cierre para el pick #%s (%s · %s · %s en %s) — "
                    "probablemente la casa ya cerró ese mercado antes de esta corrida.",
                    pick["id"], pick["event_label"], pick["market_key"], pick["selection"], pick["bookmaker"],
                )
                continue
            storage.set_closing_odds(pick["id"], price, now.isoformat())
            captured += 1

    return captured
