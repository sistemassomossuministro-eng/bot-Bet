"""Dashboard diario del acumulado del mes en curso.

A diferencia del resumen mensual (monthly.py, que solo corre el día 1 y
resume el mes YA CERRADO), esto se manda TODOS los días junto con la corrida
normal de siempre y muestra cómo va el MES EN CURSO hasta hoy: total de
picks, ganados/perdidos/pendientes, tasa de acierto, profit y ROI, más su
evolución día a día — pedido explícito del usuario (2026-09-26) para poder
seguir la rentabilidad sin esperar a fin de mes, antes de decidir si apostar
con dinero real a las recomendaciones. Ver botbet-estado-del-proyecto.md.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date
from typing import List, Optional

from .monthly import day_label, month_label
from .social_image import BADGE_LOST, BADGE_WON, TEXT_MUTED, TEXT_PRIMARY, StatTile, render_daily_dashboard_image
from .storage.db import Storage

logger = logging.getLogger(__name__)


def _format_units(value: float) -> str:
    sign = "+" if value > 0 else ("" if value < 0 else "±")
    return f"{sign}{value:.1f}u" if value != 0 else "0.0u"


def _format_pct(value: Optional[float]) -> str:
    return f"{value:.1f}%" if value is not None else "s/d"


def build_dashboard_tiles(summary: dict) -> List[StatTile]:
    """Mismo criterio que monthly.build_stat_tiles(), más un tile de
    'Pendientes' (picks del mes en curso que aún no se liquidan) — a mitad
    de mes eso sí puede ser > 0, a diferencia del resumen mensual, que solo
    resume un mes ya cerrado."""
    profit = summary["profit_units"]
    profit_color = BADGE_WON if profit > 0 else (BADGE_LOST if profit < 0 else TEXT_PRIMARY)
    roi_color = BADGE_WON if (summary["roi_pct"] or 0) > 0 else (BADGE_LOST if (summary["roi_pct"] or 0) < 0 else TEXT_PRIMARY)

    tiles = [
        StatTile("Total de picks", str(summary["total"])),
        StatTile("Ganados", str(summary["won"]), value_color=BADGE_WON),
        StatTile("Perdidos", str(summary["lost"]), value_color=BADGE_LOST),
        StatTile("Pendientes", str(summary.get("pending", 0)), value_color=TEXT_MUTED),
        StatTile("Tasa de acierto", _format_pct(summary["hit_rate_pct"])),
        StatTile("Profit (stake plano)", _format_units(profit), value_color=profit_color),
        StatTile("ROI del mes", _format_pct(summary["roi_pct"]), value_color=roi_color),
    ]

    # CLV: mismo criterio que monthly.py — solo aparece si se capturó cierre
    # para al menos 1 pick del mes en curso (ver clv.py).
    clv_sample_size = summary.get("clv_sample_size") or 0
    if clv_sample_size > 0:
        avg_clv = summary.get("avg_clv_pct")
        clv_color = BADGE_WON if (avg_clv or 0) > 0 else (BADGE_LOST if (avg_clv or 0) < 0 else TEXT_PRIMARY)
        tiles.append(StatTile("CLV promedio", f"{_format_pct(avg_clv)} ({clv_sample_size})", value_color=clv_color))

    return tiles


def dashboard_status(summary: dict) -> str:
    """'profitable' / 'negative' / 'neutral' — a diferencia del booleano
    is_profitable del resumen mensual (que solo corre sobre un mes cerrado,
    donde profit==0 exacto es rarísimo), acá 'neutral' cubre el caso normal
    de principios de mes: 0 picks decididos todavía no es lo mismo que ir
    perdiendo."""
    decided = (summary.get("won", 0) or 0) + (summary.get("lost", 0) or 0)
    if decided == 0:
        return "neutral"
    profit = summary["profit_units"]
    if profit > 0:
        return "profitable"
    if profit < 0:
        return "negative"
    return "neutral"


def generate_daily_dashboard(cfg, storage: Storage, alerter, today: Optional[date] = None) -> dict:
    """Genera y manda (si hay alerter) el dashboard del acumulado del mes en
    curso. Corre TODOS los días (no solo el día 1, a diferencia de
    generate_monthly_summary_if_due) y siempre sobre el mes EN CURSO."""
    from .daily import bogota_today  # import local: evita import circular con daily.py

    today = today or bogota_today()
    summary = storage.monthly_picks_summary(today.year, today.month)
    label = month_label(today.year, today.month)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    cut_label = f"Corte: {day_label(today)} · día {today.day}/{days_in_month}"

    tiles = build_dashboard_tiles(summary)
    status = dashboard_status(summary)
    series = storage.daily_profit_series(today.year, today.month, today.day)

    image_path = f"{cfg.output_dir}/latest_dashboard.png"
    render_daily_dashboard_image(label, cut_label, tiles, status, series, image_path)

    logger.info(
        "Dashboard diario %s (corte día %d/%d): %d picks, %d ganados, %d perdidos, %d pendientes, "
        "profit=%.2fu, roi=%s",
        label, today.day, days_in_month, summary["total"], summary["won"], summary["lost"],
        summary.get("pending", 0), summary["profit_units"], summary["roi_pct"],
    )

    if alerter:
        alerter.send_daily_dashboard_message(label, today, summary, status)
        alerter.send_photo(image_path, caption=f"Acumulado de {label} — día {today.day}/{days_in_month}")

    return summary
