"""Notificaciones por Telegram (una sola vía: informar, no ejecutar acciones).

El bot SOLO envía mensajes. No tiene botones que coloquen apuestas ni se
conecta a ninguna casa de apuestas. Confirmar/rechazar/liquidar una apuesta se
hace con la CLI (`python -m valuebet.cli`) después de que el usuario la coloque
manualmente.

Cómo crear un bot y obtener bot_token / chat_id:
1. Habla con @BotFather en Telegram -> /newbot -> te da un token.
2. Envíale un mensaje cualquiera a tu bot recién creado.
3. Visita https://api.telegram.org/bot<TU_TOKEN>/getUpdates y busca "chat":{"id": ...}
   Ese número es tu chat_id.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import requests

from ..descriptions import describe_selection
from ..models import ValueBet

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 20):
        if not bot_token or bot_token.startswith("TU_"):
            raise ValueError("Falta configurar 'alerts.telegram.bot_token' en config.yaml")
        if not chat_id or str(chat_id).startswith("TU_"):
            raise ValueError("Falta configurar 'alerts.telegram.chat_id' en config.yaml")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, text: str) -> bool:
        try:
            resp = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error("Error enviando mensaje de Telegram: %s", exc)
            return False

    def send_photo(self, photo_path: str, caption: Optional[str] = None) -> bool:
        try:
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    f"{self._base_url}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption or "", "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=self.timeout,
                )
            resp.raise_for_status()
            return True
        except (requests.RequestException, OSError) as exc:
            logger.error("Error enviando foto de Telegram (%s): %s", photo_path, exc)
            return False

    def send_value_bet(self, vb: ValueBet, db_id: Optional[int] = None) -> bool:
        header = "🎯 <b>Apuesta de valor detectada</b>"
        id_line = f"ID interno: #{db_id}\n" if db_id is not None else ""
        stake_line = (
            f"Stake sugerido: {vb.suggested_stake:,.0f}\n" if vb.suggested_stake else ""
        )
        text = (
            f"{header}\n"
            f"{id_line}"
            f"{vb.event.label()}\n"
            f"Inicia: {vb.event.commence_time.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Apuesta: <b>{vb.description()}</b>\n"
            f"Casa: <b>{vb.bookmaker}</b> @ {vb.offered_odds:.2f}\n"
            f"Prob. justa estimada: {vb.fair_probability*100:.1f}%\n"
            f"EV estimado: <b>{vb.ev_pct:.2f}%</b>\n"
            f"{stake_line}"
            f"Referencia: {', '.join(vb.reference_bookmakers)}\n\n"
            f"Esta es una sugerencia de ANÁLISIS, no una apuesta colocada. "
            f"Verifica la cuota vigente en {vb.bookmaker} antes de apostar manualmente."
        )
        return self.send(text)

    def send_daily_limit_notice(self) -> bool:
        return self.send(
            "⚠️ Se alcanzó el límite diario de pérdida configurado. "
            "Las alertas de nuevas apuestas se pausan por hoy."
        )

    def send_daily_picks_message(self, pick_date_str: str, picks: List[ValueBet]) -> bool:
        if not picks:
            return self.send(
                f"⚽ <b>Pronósticos del {pick_date_str}</b>\n\n"
                f"No se encontraron oportunidades con el EV mínimo configurado hoy."
            )
        lines = [f"⚽ <b>Pronósticos del día — {pick_date_str}</b>", f"{len(picks)} picks de fútbol\n"]
        for i, vb in enumerate(picks, start=1):
            lines.append(
                f"{i}. <b>{vb.event.label()}</b>\n"
                f"   {vb.description()} @ {vb.offered_odds:.2f} ({vb.bookmaker})\n"
                f"   EV: <b>+{vb.ev_pct:.1f}%</b>"
            )
        lines.append(
            "\nAnálisis estadístico automatizado — no coloca apuestas por ti. "
            "Verifica la cuota vigente antes de decidir. Juega con responsabilidad."
        )
        return self.send("\n".join(lines))

    def send_daily_results_message(self, pick_date_str: str, settled_rows: list, summary: dict) -> bool:
        if not settled_rows:
            return self.send(
                f"📊 <b>Resultados del {pick_date_str}</b>\n\nNo hubo picks liquidados en esta corrida."
            )
        icon = {"won": "✅", "lost": "❌", "push": "➖"}
        lines = [f"📊 <b>Resultados del {pick_date_str}</b>\n"]
        for row in settled_rows:
            mark = icon.get(row["result"], "•")
            score = (
                f" ({row['home_score']}-{row['away_score']})"
                if row["home_score"] is not None and row["away_score"] is not None
                else ""
            )
            desc = describe_selection(row["market_key"], row["selection"], row["home_team"], row["away_team"])
            lines.append(f"{mark} {row['event_label']} — {desc} @ {row['offered_odds']:.2f}{score}")
        hit_rate = summary.get("hit_rate_pct")
        hit_rate_txt = f"{hit_rate:.0f}%" if hit_rate is not None else "s/d"
        lines.append(
            f"\nAciertos: {summary.get('won', 0)}/{(summary.get('won', 0) or 0) + (summary.get('lost', 0) or 0)} "
            f"({hit_rate_txt}) · EV promedio del día: {summary.get('avg_ev_pct', 0):.1f}%"
        )
        return self.send("\n".join(lines))

    def send_monthly_summary_message(self, month_label_str: str, summary: dict, is_profitable: bool) -> bool:
        header = "📅 <b>Resumen mensual — " + month_label_str + "</b>"
        veredicto = "✅ <b>MES RENTABLE</b>" if is_profitable else "❌ <b>MES NO RENTABLE</b>"
        hit_rate = summary.get("hit_rate_pct")
        hit_rate_txt = f"{hit_rate:.1f}%" if hit_rate is not None else "s/d"
        roi = summary.get("roi_pct")
        roi_txt = f"{roi:+.1f}%" if roi is not None else "s/d"
        profit = summary.get("profit_units", 0.0)
        text = (
            f"{header}\n\n"
            f"{veredicto}\n\n"
            f"Total de picks: {summary.get('total', 0)}\n"
            f"Ganados: {summary.get('won', 0)} · Perdidos: {summary.get('lost', 0)} · "
            f"Anulados: {summary.get('push', 0)}\n"
            f"Tasa de acierto: {hit_rate_txt}\n"
            f"Profit (stake plano de 1 unidad por pick): <b>{profit:+.2f}u</b>\n"
            f"ROI del mes: <b>{roi_txt}</b>\n\n"
            f"Este cálculo asume 1 unidad apostada por pick — no es tu resultado "
            f"real de dinero si apostaste montos distintos o no tomaste todos los "
            f"picks. Análisis estadístico, no garantía de resultados futuros."
        )
        return self.send(text)
