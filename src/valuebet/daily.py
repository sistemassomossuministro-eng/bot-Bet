"""Lógica del resumen diario: selección de los N picks del día y liquidación
automática de los picks pendientes de días anteriores.

Colombia no observa horario de verano — la hora de Bogotá es siempre UTC-5,
así que se usa un offset fijo en vez de una librería de zonas horarias (evita
depender de que el runner tenga datos de tzdata instalados).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from .config import AppConfig, leagues_for_sport
from .models import Event, ValueBet
from .odds_provider import OddsApiError, OddsProvider
from .secondary_signals import enrich_picks_with_secondary_signals
from .settlement import settle_selection
from .storage.db import Storage
from .value_finder import NearMiss, collapse_correlated_h2h, collapse_correlated_totals, find_value_bets

logger = logging.getLogger(__name__)

BOGOTA_OFFSET = timedelta(hours=-5)


def bogota_today() -> date:
    return (datetime.utcnow() + BOGOTA_OFFSET).date()


ODDS_BATCH_SIZE = 10  # eventIds por llamada a /odds/multi (tope de odds-api.io)

# Presupuesto TOTAL de espera por rate limit (429) por corrida, en la fase de
# cuotas. El plan gratuito permite 100 peticiones/hora y esta corrida usa ~60
# (una por liga + los lotes de cuotas), así que un 429 casi siempre significa
# que se lanzó el workflow más de una vez en la misma hora. Visto 2026-11-06:
# 12 de 16 lotes cayeron con "se libera en 561s". Esperar ese tiempo UNA vez es
# mucho mejor que dejar el día medio evaluado; si el reinicio tarda más que el
# presupuesto, no se espera (daily.yml tiene timeout-minutes: 25).
ODDS_RATE_LIMIT_WAIT_BUDGET_SECONDS = 600.0


@dataclass
class OddsFetchDiagnostics:
    """Qué pasó al pedir las cuotas del día — existe para que un fallo de la
    API NO se confunda con "hoy no hubo valor".

    INCIDENTE REAL (2026-10-26 → 2026-11-06): odds-api.io movió Bet365 a planes
    de pago; cada lote de /odds/multi pedido con Bet365 respondió 403, el
    código solo lo registraba en el log y seguía, y el job terminaba en verde
    con 0 picks durante ~12 días sin que nada lo avisara. Con esto, run_daily_job
    manda una alerta explícita por Telegram cuando algún lote falla."""

    events_listed: int = 0
    events_with_odds: int = 0
    total_batches: int = 0
    failed_batches: int = 0
    errors: List[str] = field(default_factory=list)
    systemic_error: Optional[str] = None  # 401/402/403: reintentar igual no sirve
    rate_limited: bool = False            # 429 sin posibilidad de esperar: se cortó el resto
    rate_limit_wait_used: float = 0.0     # segundos ya esperados por 429 en esta corrida
    rate_limit_waits: int = 0
    stale_reference_count: int = 0        # mercados de referencia descartados por viejos

    @property
    def aborted(self) -> bool:
        """Ya no tiene sentido seguir pidiendo cuotas en esta corrida."""
        return bool(self.systemic_error) or self.rate_limited

    def has_problem(self) -> bool:
        return self.failed_batches > 0 or (self.events_listed > 0 and self.events_with_odds == 0)

    def alert_text(self) -> str:
        lines = []
        if self.systemic_error:
            lines.append(
                "odds-api.io rechazó el pedido de cuotas (permiso/plan). Probablemente una casa configurada "
                "ya no está incluida en tu plan gratuito, o la API key dejó de ser válida."
            )
            lines.append(f"Detalle: {self.systemic_error}")
            lines.append(
                "Revisa odds-api.io/dashboard/bookmakers y los nombres en odds_provider.target_bookmakers / "
                "reference_bookmakers de config.example.yaml."
            )
        elif self.rate_limited:
            lines.append(
                "odds-api.io alcanzó el límite de peticiones por hora de tu plan gratuito (100/h) y no se pudo "
                f"esperar a que se libere: {self.failed_batches} de {self.total_batches} lote(s) de cuotas "
                "quedaron sin evaluar. Probablemente se lanzó el workflow más de una vez en la misma hora."
            )
            if self.errors:
                lines.append(f"Detalle: {self.errors[-1]}")
            lines.append("Vuelve a lanzar 'Resumen diario' pasada una hora si quieres evaluar el resto de partidos.")
        elif self.failed_batches:
            lines.append(
                f"Fallaron {self.failed_batches} de {self.total_batches} lote(s) de cuotas; "
                "los picks de hoy pueden estar incompletos."
            )
            if self.errors:
                lines.append(f"Último error: {self.errors[-1]}")
        else:
            lines.append(
                f"Se listaron {self.events_listed} partido(s) pero ninguno devolvió cuotas de las casas "
                "configuradas. Revisa los nombres exactos en GET /bookmakers."
            )
        lines.append("Sin cuotas confiables, un 0 de picks hoy NO significa que no hubo valor.")
        return "\n".join(lines)


def _fetch_odds_in_batches(
    provider: OddsProvider,
    event_ids: List[str],
    bookmakers: List[str],
    sport: str,
    diag: OddsFetchDiagnostics,
) -> List[Event]:
    """Pide las cuotas en lotes de ODDS_BATCH_SIZE y registra en `diag` cada
    fallo. Un error puntual (5xx, timeout) solo salta ese lote; uno sistémico
    (401/402/403) corta el resto, porque todos los demás lotes pedirían lo
    mismo y fallarían igual (y gastarían cuota de la API para nada)."""
    events: List[Event] = []
    for i in range(0, len(event_ids), ODDS_BATCH_SIZE):
        chunk = event_ids[i : i + ODDS_BATCH_SIZE]
        diag.total_batches += 1
        waited_for_this_batch = False
        while True:
            try:
                events.extend(provider.get_events_odds(chunk, bookmakers, sport=sport))
                break
            except Exception as exc:
                retry_after = getattr(exc, "retry_after_seconds", None)
                if (
                    isinstance(exc, OddsApiError)
                    and exc.status_code == 429
                    and retry_after is not None
                    and not waited_for_this_batch
                    and diag.rate_limit_wait_used + retry_after + 5 <= ODDS_RATE_LIMIT_WAIT_BUDGET_SECONDS
                ):
                    wait = retry_after + 5
                    logger.warning(
                        "Rate limit de odds-api.io: esperando %.0fs para reintentar este lote "
                        "(presupuesto de espera usado: %.0f/%.0fs).",
                        wait,
                        diag.rate_limit_wait_used,
                        ODDS_RATE_LIMIT_WAIT_BUDGET_SECONDS,
                    )
                    time.sleep(wait)
                    diag.rate_limit_wait_used += wait
                    diag.rate_limit_waits += 1
                    waited_for_this_batch = True
                    continue

                diag.failed_batches += 1
                diag.errors.append(f"{sport}: {exc}")
                logger.exception("Fallo al obtener cuotas en lote para '%s' (%d eventos)", sport, len(chunk))
                if isinstance(exc, OddsApiError) and exc.is_systemic:
                    diag.systemic_error = f"HTTP {exc.status_code} — {exc.body or exc}"
                elif isinstance(exc, OddsApiError) and exc.status_code == 429:
                    diag.rate_limited = True
                break

        if diag.aborted:
            remaining = len(event_ids) - (i + ODDS_BATCH_SIZE)
            if remaining > 0:
                diag.failed_batches += -(-remaining // ODDS_BATCH_SIZE)  # lotes sin intentar
                diag.total_batches += -(-remaining // ODDS_BATCH_SIZE)
                logger.error(
                    "Se cancelan los %d evento(s) restantes de '%s' (%s).",
                    remaining,
                    sport,
                    "error sistémico de la API" if diag.systemic_error else "rate limit sin espera posible",
                )
            break
    return events


def _log_near_miss_summary(near_misses: List[NearMiss], min_ev_pct: float) -> None:
    """Resume, en una sola línea INFO, qué tan cerca estuvo del mínimo exigido
    el mejor candidato real evaluado dentro del rango de cuota — aunque haya
    terminado en 0 picks. Además, desde el 2026-09-09, agrega una línea por
    mercado (h2h/totals/btts/...) con su propio mejor EV.

    Se agregó (2026-08-29) tras varios días seguidos en 0 picks y subir
    `max_odds` dos veces sin resultado: sin esto, el log solo decía "0/10
    picks", sin decir si los candidatos que sí cayeron en rango se quedaron
    rozando el 3.0% de EV o muy lejos — cualquier ajuste de min_odds/
    max_odds/min_ev_pct se hacía a ciegas. Con esto, la próxima decisión de
    ajuste se toma con el EV real más alto encontrado ese día, no adivinando.

    El desglose por mercado se agregó porque el resumen de un solo "mejor
    candidato del día" no distingue "h2h nunca tiene candidatos" de "h2h sí
    tiene candidatos pero ninguno se acerca al mínimo" de "hoy simplemente
    ganó totals por EV, pero h2h estuvo cerca" — las tres se ven idénticas
    en el resumen general (0 picks de h2h), y sin esto había que adivinar
    cuál de las tres estaba pasando en vez de leerlo directo del log."""
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

    # Desglose por mercado — solo aparecen los mercados que de verdad tuvieron
    # al menos un candidato dentro del rango de cuota ese día (nunca se
    # inventa una línea "0 candidatos" para un mercado ausente).
    by_market: Dict[str, List[NearMiss]] = {}
    for nm in near_misses:
        by_market.setdefault(nm.market_key, []).append(nm)

    for market_key in sorted(by_market):
        group = by_market[market_key]
        best_in_market = max(group, key=lambda nm: nm.ev_pct)
        below_in_market = sum(1 for nm in group if nm.ev_pct < min_ev_pct)
        logger.info(
            "  · %s: %d candidato(s) (%d no llegaron al mínimo) — mejor EV %.2f%% — %s · %s @ %.2f (%s).",
            market_key,
            len(group),
            below_in_market,
            best_in_market.ev_pct,
            best_in_market.event_label,
            best_in_market.selection,
            best_in_market.offered_odds,
            best_in_market.bookmaker,
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


def _reference_bookmakers_for_devig(cfg: AppConfig, events: List[Event]) -> List[str]:
    """Arma la lista de libros de referencia a usar en find_value_bets — NO la
    misma que `cfg.odds_provider.reference_bookmakers` (esa se manda tal cual
    a odds-api.io como parámetro 'bookmakers' de /odds/multi, y agregar
    "Pinnacle" ahí rompería el tope de 2 bookmakers propios del plan gratis
    de odds-api.io, además de que Pinnacle NO es un bookmaker de odds-api.io).

    Si pinnacle_reference está activado, intenta enriquecer `events` EN EL
    LUGAR con los mercados de Pinnacle (ver pinnapi_provider.py) y antepone
    "Pinnacle" a la lista de referencia — find_value_bets ya usa el PRIMER
    libro de referencia que tenga el mercado completo para devigar (ver
    find_value_bets_in_event), así que anteponerlo alcanza para que Pinnacle
    tenga prioridad sobre Bet365 sin ningún caso especial: un evento/mercado
    que Pinnacle no cubra (o si la consulta entera falla) simplemente cae de
    vuelta a Bet365, porque ese partido no tendrá mercados bajo la clave
    "Pinnacle" en absoluto.

    Cualquier fallo consultando pinnapi (key inválida, red caída, forma de
    respuesta inesperada) se registra y se sigue SOLO con
    cfg.odds_provider.reference_bookmakers (hoy Unibet) — esta fuente es
    opcional y no oficial (ver PinnacleReferenceConfig), nunca debe tumbar
    el resumen diario."""
    base_reference = cfg.odds_provider.reference_bookmakers
    if not cfg.pinnacle_reference.enabled:
        return base_reference

    try:
        from .pinnapi_provider import PinnapiProvider, attach_pinnacle_reference_markets

        provider = PinnapiProvider(
            api_key=cfg.pinnacle_reference.api_key,
            base_url=cfg.pinnacle_reference.base_url,
        )
        pinnapi_events = provider.get_soccer_events()
        matched = attach_pinnacle_reference_markets(events, pinnapi_events)
        total_football = sum(1 for e in events if e.sport == "football")
        logger.info(
            "Pinnacle (vía pinnapi.com, fuente no oficial): %d/%d evento(s) de fútbol emparejados "
            "y usados como referencia preferida para h2h/totals (el resto sigue con la referencia de odds_provider.reference_bookmakers).",
            matched,
            total_football,
        )
        if total_football == 0 and events:
            # Alarma temprana: hay eventos, pero NINGUNO cuenta como "football"
            # según Event.sport. Esto ya pasó en producción una vez (ver doc
            # del proyecto, 2026-09-07) por una comparación de string frágil
            # contra un valor que en realidad venía de otra parte — si vuelve
            # a pasar, es casi seguro el mismo tipo de bug, no que de verdad no
            # haya partidos de fútbol en la ventana del día.
            logger.warning(
                "pinnacle_reference.enabled=true pero 0 de %d evento(s) totales tienen "
                "Event.sport == 'football' — la integración de Pinnacle no puede emparejar "
                "nada así haya partidos reales. Revisa cómo se está poblando Event.sport.",
                len(events),
            )
    except ValueError as exc:
        logger.warning("pinnacle_reference.enabled=true pero no se pudo inicializar (%s) — se sigue solo con la referencia de odds-api.io.", exc)
        return base_reference
    except Exception:
        logger.exception("Fallo consultando pinnapi.com para la referencia de Pinnacle — se sigue solo con la referencia de odds-api.io.")
        return base_reference

    return ["Pinnacle"] + base_reference


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
    cfg: AppConfig,
    provider: OddsProvider,
    storage: Storage,
    pick_date: Optional[date] = None,
    diagnostics: Optional[OddsFetchDiagnostics] = None,
) -> List[ValueBet]:
    """Busca value bets de fútbol para hoy, selecciona el top N y los guarda en daily_picks.

    `diagnostics`: si se pasa, se llena con lo ocurrido al pedir cuotas (ver
    OddsFetchDiagnostics) para que quien llama pueda avisar de un fallo de API."""
    diagnostics = diagnostics if diagnostics is not None else OddsFetchDiagnostics()
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
        diagnostics.events_listed += len(event_ids)
        if diagnostics.aborted:
            # Un deporte anterior ya falló por permiso/plan o por rate limit:
            # este pedirá lo mismo y fallaría igual — no se gasta cuota de API.
            continue
        # Lotes de 10 eventos por request (en vez de una request por partido) —
        # necesario para que cubrir fútbol mundial (potencialmente cientos de
        # partidos/día) no agote la cuota de la API de cuotas. Cada lote
        # fallido queda registrado en `diagnostics` (ver OddsFetchDiagnostics).
        sport_events = _fetch_odds_in_batches(provider, event_ids, all_bookmakers, sport, diagnostics)
        diagnostics.events_with_odds += len(sport_events)
        events.extend(sport_events)

    # Si pinnacle_reference.enabled=true, esto enriquece `events` EN EL LUGAR
    # con los mercados de Pinnacle (h2h/totals) donde haya podido emparejar
    # el partido, y devuelve ["Pinnacle", ...Bet365...] para que se use como
    # referencia preferida; si está desactivado o falla, devuelve la lista
    # de siempre (solo Bet365) sin tocar `events` — ver la función para el
    # detalle completo de por qué esto NO es lo mismo que
    # cfg.odds_provider.reference_bookmakers.
    reference_bookmakers = _reference_bookmakers_for_devig(cfg, events)

    # near_misses: cualquier candidato que sí cayó dentro del rango de cuota
    # (min_odds/max_odds) y sí se pudo devigar/calcular su EV real, aunque no
    # haya llegado a min_ev_pct — ver NearMiss en value_finder.py. Puramente
    # para el log de abajo, nunca se usa para elegir picks.
    near_misses: List[NearMiss] = []
    stale_reference_log: List[str] = []
    candidates = find_value_bets(
        events,
        target_bookmakers=cfg.odds_provider.target_bookmakers,
        reference_bookmakers=reference_bookmakers,
        devig_method=cfg.value_detection.devig_method,
        min_ev_pct=cfg.value_detection.min_ev_pct,
        min_reference_books=cfg.value_detection.min_reference_books,
        allowed_markets=cfg.value_detection.allowed_markets,
        max_ev_pct=cfg.value_detection.max_ev_pct,
        max_totals_point=cfg.value_detection.max_totals_point,
        min_odds=cfg.value_detection.min_odds,
        max_odds=cfg.value_detection.max_odds,
        near_misses=near_misses,
        max_reference_age_hours=cfg.value_detection.max_reference_age_hours,
        stale_reference_log=stale_reference_log,
        # Nota: aquí NO se aplican límites de banca/Kelly — el resumen diario es
        # una lista informativa de oportunidades, no una secuencia de apuestas
        # ejecutadas automáticamente una tras otra.
    )
    _log_near_miss_summary(near_misses, cfg.value_detection.min_ev_pct)
    unique_stale = sorted(set(stale_reference_log))
    diagnostics.stale_reference_count = len(unique_stale)
    if unique_stale:
        logger.info(
            "%d mercado(s) de referencia ignorados por tener más de %.0f h sin actualizarse "
            "(value_detection.max_reference_age_hours). Primeros: %s",
            len(unique_stale),
            cfg.value_detection.max_reference_age_hours,
            "; ".join(unique_stale[:5]),
        )

    # Varias líneas de 'totals' del mismo lado (over/under) en el mismo
    # partido son, en la práctica, la misma apuesta a distintos umbrales —
    # ver collapse_correlated_totals en value_finder.py. Se aplica ANTES del
    # dedupe entre corridas de abajo (ese es por evento+fecha, este es
    # dentro de una sola corrida) y antes de select_daily_picks, para que el
    # top N no se llene con lo que en la práctica es una sola posición.
    before_collapse = len(candidates)
    candidates = collapse_correlated_totals(candidates)
    collapsed = before_collapse - len(candidates)
    if collapsed:
        logger.info(
            "%d candidato(s) de 'totals' descartados por ser líneas más extremas que otra del mismo "
            "partido y lado ya elegida (ver detalle arriba)",
            collapsed,
        )

    # Mismo problema de fondo que collapse_correlated_totals, pero para h2h:
    # el motor puede encontrar valor en dos resultados mutuamente excluyentes
    # del mismo partido a la vez (ej. "gana Nacional" y "gana América" del
    # mismo Nacional vs América) — collapse_correlated_h2h se queda con el de
    # mayor probabilidad "justa" estimada (ver value_finder.py). Pedido
    # explícito del usuario (2026-09-22) para poder medir rentabilidad
    # mensual sin picks contradictorios del mismo partido.
    before_h2h_collapse = len(candidates)
    candidates = collapse_correlated_h2h(candidates)
    h2h_collapsed = before_h2h_collapse - len(candidates)
    if h2h_collapsed:
        logger.info(
            "%d candidato(s) de 'h2h' descartados por haber otro resultado del mismo partido con "
            "mayor probabilidad de éxito (ver detalle arriba)",
            h2h_collapsed,
        )

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
