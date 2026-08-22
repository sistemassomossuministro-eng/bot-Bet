"""Resumen mensual automático: el día 1 de cada mes, resume el desempeño de
los picks del mes calendario que acaba de terminar (total, ganados, perdidos,
anulados, y si el mes fue rentable o no bajo el supuesto de stake plano —
ver el docstring de `Storage.monthly_picks_summary`).

Se engancha en `daily_job.py`: cada corrida diaria revisa si hoy es día 1 y,
si lo es, además del ciclo normal genera y envía este resumen. No hace falta
un segundo workflow de GitHub Actions ni un segundo cron.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from .social_image import StatTile, render_monthly_summary_image
from .social_publish import queue_instagram_image
from .storage.db import Storage

logger = logging.getLogger(__name__)

_MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def month_label(year: int, month: int) -> str:
    return f"{_MESES_ES[month].capitalize()} {year}"


def previous_month(today: date) -> "tuple[int, int]":
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_prev_month.year, last_day_prev_month.month


def is_first_of_month(today: date) -> bool:
    return today.day == 1


def _format_units(value: float) -> str:
    sign = "+" if value > 0 else ("" if value < 0 else "±")
    return f"{sign}{value:.1f}u" if value != 0 else "0.0u"


def _format_pct(value: Optional[float]) -> str:
    return f"{value:.1f}%" if value is not None else "s/d"


def build_stat_tiles(summary: dict) -> list:
    from .social_image import BADGE_LOST, BADGE_WON, TEXT_PRIMARY

    profit = summary["profit_units"]
    profit_color = BADGE_WON if profit > 0 else (BADGE_LOST if profit < 0 else TEXT_PRIMARY)
    roi_color = BADGE_WON if (summary["roi_pct"] or 0) > 0 else (BADGE_LOST if (summary["roi_pct"] or 0) < 0 else TEXT_PRIMARY)

    return [
        StatTile("Total de picks", str(summary["total"])),
        StatTile("Ganados", str(summary["won"]), value_color=BADGE_WON),
        StatTile("Perdidos", str(summary["lost"]), value_color=BADGE_LOST),
        StatTile("Tasa de acierto", _format_pct(summary["hit_rate_pct"])),
        StatTile("Profit (stake plano)", _format_units(profit), value_color=profit_color),
        StatTile("ROI del mes", _format_pct(summary["roi_pct"]), value_color=roi_color),
    ]


def _monthly_caption(label: str, summary: dict, is_profitable: bool) -> str:
    from .branding import BRAND_NAME

    estado = "rentable" if is_profitable else "no rentable"
    return (
        f"Resumen de {label}: {summary['total']} picks, {summary['won']} ganados, "
        f"{summary['lost']} perdidos. Mes {estado} con stake plano de 1u por pick "
        f"({_format_units(summary['profit_units'])}).\n\n"
        "Cálculo con stake plano (1 unidad por pick), no representa tu banca real. "
        "Análisis estadístico automatizado, no es garantía de resultado futuro. Juega con "
        "responsabilidad. +18.\n\n"
        f"#{BRAND_NAME} #ResumenMensual #FutbolMundial #ValueBetting"
    )


def generate_monthly_summary_if_due(
    cfg,
    storage: Storage,
    alerter,
    today: Optional[date] = None,
    instagram_queue: Optional[list] = None,
) -> Optional[dict]:
    """Si hoy es día 1, genera y envía el resumen del mes que acaba de
    terminar. Cualquier otro día, no hace nada y devuelve None."""
    from .daily import bogota_today  # import local: evita import circular con daily.py

    today = today or bogota_today()
    if not is_first_of_month(today):
        return None

    year, month = previous_month(today)
    summary = storage.monthly_picks_summary(year, month)
    label = month_label(year, month)

    if summary["total"] == 0:
        logger.info("Resumen mensual de %s: no hubo picks registrados, se omite.", label)
        return summary

    tiles = build_stat_tiles(summary)
    is_profitable = summary["profit_units"] > 0

    image_path = f"{cfg.output_dir}/latest_monthly_summary.png"
    render_monthly_summary_image(label, tiles, is_profitable, image_path)

    logger.info(
        "Resumen mensual %s: %d picks, %d ganados, %d perdidos, profit=%.2fu, roi=%s",
        label,
        summary["total"],
        summary["won"],
        summary["lost"],
        summary["profit_units"],
        summary["roi_pct"],
    )

    if alerter:
        alerter.send_monthly_summary_message(label, summary, is_profitable)
        alerter.send_photo(image_path, caption=f"Resumen de {label}")

    queue_instagram_image(
        cfg, instagram_queue, "monthly", image_path, _monthly_caption(label, summary, is_profitable)
    )

    return summary
