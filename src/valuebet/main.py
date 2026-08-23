"""Orquestador principal: busca apuestas de valor y notifica. No coloca apuestas.

Uso:
    python -m valuebet.main --config config.yaml [--once]

--once ejecuta un solo ciclo (útil para probar o para correrlo vía cron)
en vez de quedarse en loop con `poll_interval_seconds`.
"""
from __future__ import annotations

import argparse
import logging
import time

from .config import leagues_for_sport, load_config, setup_logging
from .odds_provider import build_provider
from .storage.db import Storage
from .value_finder import find_value_bets

try:
    from .alerts.telegram_bot import TelegramAlerter
except ImportError:  # requests no instalado, etc.
    TelegramAlerter = None  # type: ignore

logger = logging.getLogger(__name__)


def run_cycle(cfg, provider, storage, alerter) -> int:
    """Ejecuta un ciclo completo: fetch -> value bets -> guardar -> alertar.
    Devuelve la cantidad de nuevas oportunidades encontradas."""
    all_target_and_ref = list(set(cfg.odds_provider.target_bookmakers + cfg.odds_provider.reference_bookmakers))

    # leagues=[] (por defecto) = todas las ligas de fútbol del mundo.
    events_with_odds = []
    for sport in cfg.odds_provider.sports:
        try:
            stub_events = provider.list_events(
                sport=sport,
                leagues=leagues_for_sport(cfg.odds_provider, sport),
                lookahead_days=cfg.odds_provider.lookahead_days,
            )
        except Exception:
            logger.exception("Fallo al listar eventos para deporte '%s'", sport)
            continue

        event_ids = [stub.event_id for stub in stub_events]
        try:
            # Lote de hasta 10 eventos por request (GET /odds/multi) en vez de
            # una request por partido — con cobertura mundial puede haber
            # cientos de partidos por ciclo.
            events_with_odds.extend(provider.get_events_odds(event_ids, all_target_and_ref))
        except Exception:
            logger.exception("Fallo al obtener cuotas en lote para '%s' (%d eventos)", sport, len(event_ids))

    staked_today = storage.staked_today()
    pnl_today = storage.pnl_today()

    limits = cfg.bankroll
    value_bets = find_value_bets(
        events_with_odds,
        target_bookmakers=cfg.odds_provider.target_bookmakers,
        reference_bookmakers=cfg.odds_provider.reference_bookmakers,
        devig_method=cfg.value_detection.devig_method,
        min_ev_pct=cfg.value_detection.min_ev_pct,
        min_reference_books=cfg.value_detection.min_reference_books,
        limits=limits,
        staked_today=staked_today,
        pnl_today=pnl_today,
        allowed_markets=cfg.value_detection.allowed_markets,
        max_ev_pct=cfg.value_detection.max_ev_pct,
        max_totals_point=cfg.value_detection.max_totals_point,
    )

    if pnl_today <= -abs(limits.daily_loss_limit_pct) * limits.total and alerter:
        alerter.send_daily_limit_notice()

    new_count = 0
    for vb in value_bets:
        bet_id, is_new = storage.upsert_pending(vb)
        logger.info("Value bet: %s", vb.summary().replace("\n", " | "))
        if is_new:
            new_count += 1
            if alerter:
                alerter.send_value_bet(vb, db_id=bet_id)

    return new_count


def main():
    parser = argparse.ArgumentParser(description="Bot de análisis de apuestas de valor (solo lectura)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="Ejecutar un solo ciclo y salir")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)

    provider = build_provider(
        {
            "name": cfg.odds_provider.name,
            "api_key": cfg.odds_provider.api_key,
            "base_url": cfg.odds_provider.base_url,
        }
    )
    storage = Storage(cfg.db_path)

    alerter = None
    if cfg.telegram and cfg.telegram.enabled and TelegramAlerter:
        alerter = TelegramAlerter(cfg.telegram.bot_token, cfg.telegram.chat_id)

    logger.info(
        "Iniciando valuebet-bot | bookmakers objetivo=%s | referencia=%s | min_ev=%.1f%%",
        cfg.odds_provider.target_bookmakers,
        cfg.odds_provider.reference_bookmakers,
        cfg.value_detection.min_ev_pct,
    )

    if args.once:
        n = run_cycle(cfg, provider, storage, alerter)
        logger.info("Ciclo único completado. %d oportunidades encontradas/actualizadas.", n)
        return

    while True:
        try:
            n = run_cycle(cfg, provider, storage, alerter)
            logger.info("Ciclo completado. %d oportunidades encontradas/actualizadas.", n)
        except Exception:
            logger.exception("Error inesperado en el ciclo principal")
        time.sleep(cfg.odds_provider.poll_interval_seconds)


if __name__ == "__main__":
    main()
