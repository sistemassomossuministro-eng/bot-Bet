"""Job diario único: se ejecuta una vez al día (vía GitHub Actions).

Hace, en orden:
  1. Liquida los picks pendientes de días anteriores usando el marcador final
     real (settlement automático) y envía el resumen de "resultados de ayer"
     por Telegram junto con la imagen para redes sociales.
  2. Si hoy es día 1 del mes, genera y envía el resumen del mes que acaba de
     terminar (total de picks, ganados/perdidos, y si fue rentable o no).
  3. Genera los picks de hoy (top N por EV, de los deportes configurados) y los envía por Telegram
     junto con la imagen para redes sociales.
  4. Si Instagram está configurado, encola en un manifiesto JSON (output/) las
     imágenes generadas en esta corrida para publicarlas en Instagram — la
     publicación real la hace scripts/publish_instagram.py, en un paso
     posterior del workflow, después de que las imágenes ya se subieron a
     GitHub (ver ese script y social_publish.py para el porqué).

No coloca apuestas. Pensado para correr desde un cron externo (GitHub Actions)
una vez al día; también se puede correr a mano para probar.
"""
from __future__ import annotations

import argparse
import logging

from .branding import BRAND_NAME
from .config import load_config, setup_logging
from .daily import bogota_today, generate_daily_picks, settle_pending_daily_picks
from .descriptions import describe_selection
from .monthly import generate_monthly_summary_if_due
from .odds_provider import build_provider
from .social_image import PickRow, render_picks_image, render_results_image, result_badge
from .social_publish import queue_instagram_image, write_manifest
from .storage.db import Storage

try:
    from .alerts.telegram_bot import TelegramAlerter
except ImportError:  # pragma: no cover
    TelegramAlerter = None  # type: ignore

logger = logging.getLogger(__name__)


def _rows_for_picks_image(picks) -> list:
    return [
        PickRow(
            match_label=vb.event.label(),
            detail=f"{vb.description()} @ {vb.offered_odds:.2f} ({vb.bookmaker})",
            right_text=f"+{vb.ev_pct:.1f}% EV",
        )
        for vb in picks
    ]


def _rows_for_results_image(settled_rows) -> list:
    rows = []
    for r in settled_rows:
        color, label = result_badge(r["result"])
        score = f" ({r['home_score']}-{r['away_score']})" if r["home_score"] is not None else ""
        desc = describe_selection(r["market_key"], r["selection"], r["home_team"], r["away_team"])
        rows.append(
            PickRow(
                match_label=r["event_label"],
                detail=f"{desc} @ {r['offered_odds']:.2f}{score}",
                right_text=label,
                right_color=color,
            )
        )
    return rows


def _picks_caption(date_str: str, count: int) -> str:
    return (
        f"Pronósticos del {date_str} — {count} análisis deportivos ordenados por valor "
        "esperado (EV) frente a un libro de referencia.\n\n"
        "Esto NO es una recomendación de apuesta ni garantiza un resultado: es un ranking "
        "estadístico automatizado. Juega con responsabilidad. +18.\n\n"
        f"#{BRAND_NAME} #Pronosticos #ValueBetting"
    )


def _results_caption(date_str: str, summary: dict) -> str:
    decided = (summary.get("won", 0) or 0) + (summary.get("lost", 0) or 0)
    return (
        f"Resultados del {date_str} — {summary.get('won', 0)}/{decided} aciertos entre los picks "
        "publicados ese día.\n\n"
        "Histórico transparente, ganemos o perdamos. Análisis estadístico automatizado, no es "
        "garantía de resultado futuro. +18.\n\n"
        f"#{BRAND_NAME} #Resultados"
    )


def run_daily_job(cfg, provider, storage, alerter) -> None:
    today = bogota_today()
    instagram_queue: list = []

    # 1) Liquidar picks pendientes de días anteriores. Normalmente esto es
    # solo "ayer", pero si el job no corrió uno o más días (falla del cron,
    # mantenimiento, etc.) puede haber varias fechas pendientes a la vez —
    # se agrupan y se manda un resumen por cada fecha, en orden.
    settled_rows = settle_pending_daily_picks(cfg, provider, storage, today)
    if settled_rows:
        by_date: dict = {}
        for row in settled_rows:
            by_date.setdefault(row["pick_date"], []).append(row)

        for pick_date_str in sorted(by_date):
            rows_for_date = by_date[pick_date_str]
            summary = storage.daily_picks_summary(pick_date_str)
            logger.info("Liquidados %d picks de %s: %s", len(rows_for_date), pick_date_str, summary)

            results_rows = _rows_for_results_image(rows_for_date)
            image_path = f"{cfg.output_dir}/latest_results.png"
            decided = (summary.get("won", 0) or 0) + (summary.get("lost", 0) or 0)
            render_results_image(
                pick_date_str,
                results_rows,
                f"{summary.get('won', 0)}/{decided} aciertos",
                image_path,
            )

            if alerter:
                alerter.send_daily_results_message(pick_date_str, rows_for_date, summary)
                alerter.send_photo(image_path, caption=f"Resultados del {pick_date_str}")

            queue_instagram_image(
                cfg, instagram_queue, "results", image_path, _results_caption(pick_date_str, summary)
            )
    else:
        logger.info("No había picks pendientes de liquidar antes de %s.", today.isoformat())

    # 2) Si hoy es día 1, cerrar el mes anterior con su propio resumen.
    # Va después de liquidar "ayer" a propósito: si ayer fue el último día del
    # mes pasado, ya quedó liquidado arriba antes de calcular el resumen.
    generate_monthly_summary_if_due(cfg, storage, alerter, today, instagram_queue=instagram_queue)

    # 3) Generar los picks de hoy.
    picks = generate_daily_picks(cfg, provider, storage, today)
    today_str = today.isoformat()
    logger.info("Generados %d picks para %s.", len(picks), today_str)

    picks_rows = _rows_for_picks_image(picks)
    picks_image_path = f"{cfg.output_dir}/latest_picks.png"
    render_picks_image(today_str, picks_rows, picks_image_path)

    if alerter:
        alerter.send_daily_picks_message(today_str, picks)
        if picks:
            alerter.send_photo(picks_image_path, caption=f"Pronósticos del {today_str}")

    if picks:
        queue_instagram_image(cfg, instagram_queue, "picks", picks_image_path, _picks_caption(today_str, len(picks)))

    # 4) Volcar la cola de Instagram del día a un manifiesto — lo publica de
    # verdad scripts/publish_instagram.py, DESPUÉS de que este mismo workflow
    # haga commit/push (las imágenes tienen que existir en GitHub primero).
    if getattr(cfg, "instagram", None) and cfg.instagram.enabled:
        write_manifest(cfg.output_dir, instagram_queue)


def main():
    parser = argparse.ArgumentParser(description="Job diario: liquida ayer + genera picks de hoy")
    parser.add_argument("--config", default="config.yaml")
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
    else:
        logger.warning("Telegram no está configurado — el job correrá pero no enviará mensajes.")

    run_daily_job(cfg, provider, storage, alerter)


if __name__ == "__main__":
    main()
