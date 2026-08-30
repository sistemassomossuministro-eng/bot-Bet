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
from .secondary_signals import enrich_picks_with_secondary_signals
from .settlement import settle_selection
from .storage.db import Storage
from .value_finder import NearMiss, find_value_bets

logger = logging.getLogger(__name__)

BOGOTA_OFFSET = timedelta(hours=-5)


def bogota_today() -> date:
    return (datetime.utcnow() + BOGOTA_OFFSET).date()


def _log_near_miss_summary(near_misses: List[NearMiss], min_ev_pct: float) -> None:
    """Resume, en una sola línea INFO, qué tan cerca estuvo del mínimo exigido
    el mejor candidato real evaluado dentro del rango de cuota — aunque haya
    terminado en 0 picks.

    Se agregó (2026-08-29) tras varios días seguidos en 0 picks y subir
    `max_odds` dos veces sin resultado: sin esto, el log solo decía "0/10
    picks", sin decir si los candidatos que sí cayeron en rango se quedaron
    rozando el 3.0% de EV o muy lejos — cualquier ajuste de min_odds/
    max_odds/min_ev_pct se hacía a ciegas. Con esto, la próxima decisión de
    ajuste se toma con el EV real más alto encontrado ese día, no adivinando."""
    if not near_misses:
        logger.info(
            "Ningún candidato cayó dentro del rango de cuota configurado (min_odds/max_odds) hoy — "
            "no hubo nada que evaluar contra el EV mínimo de %.1f%%.",
            min_ev_pct,
        )
        return

    best = max(near_misses, key=lambda nm: nm.ev_pct)
    below = sum(1 for nm in near_misses if nm.ev_pct < min_ev_pct)
    logger.info(
        "%d candidato(s) evaluados dentro del rango de cuota (%d no llegaron al EV mínimo de %.1f%%). "
        "Mejor EV real encontrado: %.2f%% — %s · %s · %s @ %.2f (%s).",
        len(near_misses),
        below,
        min_ev_pct,
        best.ev_pct,
        best.event_label,
        best.market_key,
        best.selection,
        best.offered_odds,
        best.bookmaker,
    )


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


def _enrich_picks_with_secondary_signals_safely(cfg: AppConfig, picks: List[ValueBet]) -> None:
    """Construye los providers de PlayerElo/API-Football (si están activados
    y con api_key real) y enriquece `picks` en el lugar. Cualquier fallo acá
    — key faltante, API caída, lo que sea — se registra y se sigue sin la
    señal, nunca tumba el resumen diario (ver secondary_signals.py)."""
    if not picks:
        return

    playerelo_provider = None
    if cfg.secondary_signals.playerelo.enabled:
        try:
            from .playerelo_provider import PlayerEloProvider

            playerelo_provider = PlayerEloProvider(
                api_key=cfg.secondary_signals.playerelo.api_key,
                base_url=cfg.secondary_signals.playerelo.base_url,
            )
        except ValueError as exc:
            logger.warning("secondary_signals.playerelo.enabled=true pero no se pudo inicializar (%s) — se omite.", exc)

    injuries_provider = None
    if cfg.secondary_signals.injuries.enabled:
        try:
            from .injuries_provider import ApiFootballProvider

            injuries_provider = ApiFootballProvider(
                api_key=cfg.secondary_signals.injuries.api_key,
                base_url=cfg.secondary_signals.injuries.base_url,
                via_rapidapi=cfg.secondary_signals.injuries.via_rapidapi,
            )
        except ValueError as exc:
            logger.warning("secondary_signals.injuries.enabled=true pero no se pudo inicializar (%s) — se omite.", exc)

    if playerelo_provider is None and injuries_provider is None:
        return

    try:
        enrich_picks_with_secondary_signals(
            picks, cfg, playerelo_provider=playerelo_provider, injuries_provider=injuries_provider
        )
    except Exception:
        logger.exception("Fallo enriqueciendo picks con señales secundarias — se sigue sin ellas.")


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

    # near_misses: cualquier candidato que sí cayó dentro del rango de cuota
    # (min_odds/max_odds) y sí se pudo devigar/calcular su EV real, aunque no
    # haya llegado a min_ev_pct — ver NearMiss en value_finder.py. Puramente
    # para el log de abajo, nunca se usa para elegir picks.
    near_misses: List[NearMiss] = []
    candidates = find_value_bets(
        events,
        target_bookmakers=cfg.odds_provider.target_bookmakers,
        reference_bookmakers=cfg.odds_provider.reference_bookmakers,
        devig_method=cfg.value_detection.devig_method,
        min_ev_pct=cfg.value_detection.min_ev_pct,
        min_reference_books=cfg.value_detection.min_reference_books,
        allowed_markets=cfg.value_detection.allowed_markets,
        max_ev_pct=cfg.value_detection.max_ev_pct,
        max_totals_point=cfg.value_detection.max_totals_point,
        min_odds=cfg.value_detection.min_odds,
        max_odds=cfg.value_detection.max_odds,
        near_misses=near_misses,
        # Nota: aquí NO se aplican límites de banca/Kelly — el resumen diario es
        # una lista informativa de oportunidades, no una secuencia de apuestas
        # ejecutadas automáticamente una tras otra.
    )
    _log_near_miss_summary(near_misses, cfg.value_detection.min_ev_pct)

    # Con daily.lookahead_days > 1, un mismo partido lejano puede seguir
    # cumpliendo las reglas de valor varias corridas seguidas antes de
    # jugarse — sin este filtro, se recomendaría el mismo evento 2-3 veces
    # por Telegram en días distintos (ver Storage.recent_daily_pick_event_ids).
    # Ventana de exclusión = lookahead_days - 1 días hacia atrás: es lo
    # máximo que un mismo evento podría haber sido visible en una corrida
    # anterior dentro de la ventana actual.
    if cfg.daily.lookahead_days > 1:
        since = (pick_date - timedelta(days=cfg.daily.lookahead_days - 1)).isoformat()
        already_picked_event_ids = storage.recent_daily_pick_event_ids(since)
        before = len(candidates)
        candidates = [c for c in candidates if c.event.event_id not in already_picked_event_ids]
        skipped = before - len(candidates)
        if skipped:
            logger.info(
                "%d candidato(s) omitido(s) por ya haber sido recomendados en una corrida anterior "
                "(mismo partido, dentro de la ventana de %d día(s))",
                skipped,
                cfg.daily.lookahead_days,
            )

    picks = select_daily_picks(candidates, cfg.daily.num_picks, cfg.daily.max_picks_per_event)

    _enrich_picks_with_secondary_signals_safely(cfg, picks)

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
        # Bug real observado en producción (ago-2026): antes, si esta consulta
        # fallaba (ej. 404 "Event not found" — odds-api.io ya purgó el evento
        # de su historial), el código hacía `continue` inmediatamente, SIN
        # pasar por el chequeo de antigüedad de abajo. Eso significaba que un
        # pick cuyo evento ya no existe en el proveedor quedaba pendiente PARA
        # SIEMPRE: cada corrida diaria repetía la misma consulta, fallaba
        # igual, y volvía a loguear el mismo traceback como ERROR sin parar —
        # nunca llegaba a `settlement_max_age_days` para expirar. Ahora, una
        # consulta fallida se trata igual que "aún sin resultado": sigue
        # intentando en corridas futuras, pero si se pasa de
        # settlement_max_age_days también se marca 'unsettled_expired' como
        # cualquier otro pick que nunca liquida.
        result_data = None
        try:
            result_data = provider.get_event_result(row["event_id"])
        except Exception:
            logger.exception("Fallo al consultar resultado del evento %s", row["event_id"])

        if result_data is not None and result_data.is_settled:
            outcome, detail = settle_selection(
                row["market_key"], row["selection"], result_data.home_score, result_data.away_score
            )
            storage.settle_daily_pick(
                row["id"], outcome, result_data.home_score, result_data.away_score
            )
            logger.info("Pick #%s liquidado: %s (%s)", row["id"], outcome, detail)
            newly_settled.append(storage.get_daily_pick(row["id"]))
            continue

        # Aún no hay resultado (o falló la consulta). Si lleva demasiados días
        # pendiente (partido aplazado/cancelado, el proveedor nunca publicó el
        # marcador, o el evento ya no existe en el proveedor), se marca para
        # no acumular pendientes eternos.
        pick_day = date.fromisoformat(row["pick_date"])
        age_days = (today - pick_day).days
        if age_days > cfg.daily.settlement_max_age_days:
            storage.settle_daily_pick(row["id"], "unsettled_expired")
            logger.warning("Pick #%s expirado sin resultado tras %d días", row["id"], age_days)

    return newly_settled
