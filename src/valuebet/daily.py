"""Lógica del resumen diario: selección de los N picks del día y liquidación
automática de los picks pendientes de días anteriores.

Colombia no observa horario de verano — la hora de Bogotá es siempre UTC-5,
así que se usa un offset fijo en vez de una librería de zonas horarias (evita
depender de que el runner tenga datos de tzdata instalados).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from .config import AppConfig, leagues_for_sport
from .models import Event, ValueBet
from .odds_provider import OddsProvider
from .settlement import settle_selection
from .storage.db import Storage
from .value_finder import find_value_bets

logger = logging.getLogger(__name__)

BOGOTA_OFFSET = timedelta(hours=-5)


def bogota_today() -> date:
    return (datetime.utcnow() + BOGOTA_OFFSET).date()


def select_daily_picks(
    value_bets: List[ValueBet], num_picks: int, max_per_event: int = 1
) -> List[ValueBet]:
    """Toma los mejores `num_picks` por EV, diversificando por partido cuando se puede.

    Primero intenta llenar los cupos respetando `max_per_event` (para no terminar
    con los 10 picks del mismo partido). Si no alcanzan picks distintos para
    llegar a `num_picks`, rellena con los siguientes mejores aunque repitan evento.
    """
    ordered = sorted(value_bets, key=lambda vb: vb.ev_pct, reverse=True)

    picks: List[ValueBet] = []
    used_events: dict[str, int] = {}
    leftovers: List[ValueBet] = []

    for vb in ordered:
        count = used_events.get(vb.event.event_id, 0)
        if count < max_per_event:
            picks.append(vb)
            used_events[vb.event.event_id] = count + 1
        else:
            leftovers.append(vb)
        if len(picks) >= num_picks:
            return picks[:num_picks]

    for vb in leftovers:
        if len(picks) >= num_picks:
            break
        picks.append(vb)

    return picks[:num_picks]


def generate_daily_picks(
    cfg: AppConfig, provider: OddsProvider, storage: Storage, pick_date: Optional[date] = None
) -> List[ValueBet]:
    """Busca value bets de fútbol para hoy, selecciona el top N y los guarda en daily_picks."""
    pick_date = pick_date or bogota_today()
    pick_date_str = pick_date.isoformat()

    all_bookmakers = list(set(cfg.odds_provider.target_bookmakers + cfg.odds_provider.reference_bookmakers))

    # leagues=[] (por defecto del proyecto) = TODAS las ligas de fútbol del
    # mundo para las que el proveedor tenga datos, no solo Colombia. Si hay
    # más de un deporte en 'sports', cada uno puede tener su propio filtro de
    # ligas vía leagues_by_sport (ver config.py) — así fútbol puede quedar
    # abierto a todas las ligas mientras basketball se restringe solo a la NBA.
    events: List[Event] = []
    for sport in cfg.odds_provider.sports:
        try:
            stub_events = provider.list_events(
                sport=sport,
                leagues=leagues_for_sport(cfg.odds_provider, sport),
                lookahead_days=cfg.daily.lookahead_days,
                limit=cfg.daily.max_events_per_run,
            )
        except Exception:
            logger.exception("Fallo al listar eventos de '%s' para el resumen diario", sport)
            continue

        if len(stub_events) >= cfg.daily.max_events_per_run:
            logger.warning(
                "Se alcanzó el tope daily.max_events_per_run=%d para '%s' — puede haber más partidos "
                "en el mundo que no se evaluaron en esta corrida. Sube el tope si tu plan de API lo permite.",
                cfg.daily.max_events_per_run,
                sport,
            )

        event_ids = [stub.event_id for stub in stub_events]
        try:
            # Una sola tanda de llamadas (10 eventos por request) en vez de una
            # request por partido — necesario para que cubrir fútbol mundial
            # (potencialmente cientos de partidos/día) no agote la cuota de la
            # API de cuotas.
            events.extend(provider.get_events_odds(event_ids, all_bookmakers))
        except Exception:
            logger.exception("Fallo al obtener cuotas en lote para '%s' (%d eventos)", sport, len(event_ids))

    candidates = find_value_bets(
        events,
        target_bookmakers=cfg.odds_provider.target_bookmakers,
        reference_bookmakers=cfg.odds_provider.reference_bookmakers,
        devig_method=cfg.value_detection.devig_method,
        min_ev_pct=cfg.value_detection.min_ev_pct,
        min_reference_books=cfg.value_detection.min_reference_books,
        allowed_markets=cfg.value_detection.allowed_markets,
        max_ev_pct=cfg.value_detection.max_ev_pct,
        # Nota: aquí NO se aplican límites de banca/Kelly — el resumen diario es
        # una lista informativa de oportunidades, no una secuencia de apuestas
        # ejecutadas automáticamente una tras otra.
    )

    picks = select_daily_picks(candidates, cfg.daily.num_picks, cfg.daily.max_picks_per_event)

    for vb in picks:
        storage.add_daily_pick(pick_date_str, vb)

    if len(picks) < cfg.daily.num_picks:
        logger.warning(
            "Solo se encontraron %d/%d picks con EV >= %.1f%% para el %s",
            len(picks),
            cfg.daily.num_picks,
            cfg.value_detection.min_ev_pct,
            pick_date_str,
        )

    return picks


def settle_pending_daily_picks(cfg: AppConfig, provider: OddsProvider, storage: Storage, today: Optional[date] = None):
    """Revisa los picks pendientes de días anteriores y los liquida si ya hay marcador final.

    Devuelve la lista de filas (sqlite3.Row) que quedaron liquidadas en esta corrida
    (útil para armar el mensaje/imagen de "resultados de ayer" sin tener que
    volver a consultar la base de datos).
    """
    today = today or bogota_today()
    pending = storage.list_pending_picks_before(today.isoformat())
    newly_settled = []

    for row in pending:
        try:
            result_data = provider.get_event_result(row["event_id"])
        except Exception:
            logger.exception("Fallo al consultar resultado del evento %s", row["event_id"])
            continue

        if result_data.is_settled:
            outcome, detail = settle_selection(
                row["market_key"], row["selection"], result_data.home_score, result_data.away_score
            )
            storage.settle_daily_pick(
                row["id"], outcome, result_data.home_score, result_data.away_score
            )
            logger.info("Pick #%s liquidado: %s (%s)", row["id"], outcome, detail)
            newly_settled.append(storage.get_daily_pick(row["id"]))
            continue

        # Aún no hay resultado. Si lleva demasiados días pendiente (partido
        # aplazado/cancelado o el proveedor nunca publicó el marcador), se
        # marca para no acumular pendientes eternos.
        pick_day = date.fromisoformat(row["pick_date"])
        age_days = (today - pick_day).days
        if age_days > cfg.daily.settlement_max_age_days:
            storage.settle_daily_pick(row["id"], "unsettled_expired")
            logger.warning("Pick #%s expirado sin resultado tras %d días", row["id"], age_days)

    return newly_settled
