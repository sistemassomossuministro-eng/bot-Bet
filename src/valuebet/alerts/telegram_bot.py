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

import html
import logging
from typing import List, Optional

import requests

from ..descriptions import describe_selection
from ..models import ValueBet

logger = logging.getLogger(__name__)

# Límite duro de la API de Telegram para sendMessage (4096 caracteres) — no
# documentado en ningún lugar del código hasta ahora porque nunca se había
# generado un mensaje que se le acercara. Se deja margen (no se usa el límite
# exacto) para no rozarlo con la variación normal de nombres de equipo/liga.
_TELEGRAM_SAFE_MESSAGE_LENGTH = 3500


def _esc(value: object) -> str:
    """Escapa '<', '>' y '&' antes de meter un texto en un mensaje con
    parse_mode='HTML'.

    INCIDENTE REAL de producción (2026-09-12): el primer día que el bot
    generó 10 picks reales de golpe (tras arreglar Pinnacle + el rate
    limit), el envío a Telegram falló con '400 Bad Request'. El log de esa
    corrida no incluía el cuerpo de la respuesta de Telegram (ya corregido,
    ver `send()`/`send_photo()` abajo), así que la causa EXACTA de esa
    corrida puntual no quedó confirmada — pero nombres de equipo/liga
    (odds-api.io) y notas de PlayerElo/lesiones (API-Football) nunca se
    habían sometido a un mensaje HTML-parseado con contenido real tan
    variado, y cualquiera de ellos con un '&', '<' o '>' literal (nunca
    verificado que no pueda pasar) rompe el parser de Telegram y tumba el
    mensaje ENTERO, no solo esa línea — es un riesgo real e independiente
    de si fue o no la causa de ESTE incidente puntual. Nunca se debe confiar
    en que un nombre externo viene "limpio" de HTML.
    """
    return html.escape(str(value), quote=False)


def _chunk_pick_blocks(blocks: List[str], max_chars: int) -> List[List[str]]:
    """Agrupa bloques de texto (un pick ya formateado cada uno) en listas que,
    unidas con '\\n', no superen `max_chars` — sin partir ningún bloque a la
    mitad. Nunca devuelve una lista vacía de chunks (al menos uno, aunque
    venga vacío), para que el llamador siempre tenga al menos un mensaje que
    mandar."""
    chunks: List[List[str]] = []
    current: List[str] = []
    current_len = 0
    for block in blocks:
        block_len = len(block) + 1  # +1 por el "\n" que lo une al resto
        if current and current_len + block_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current or not chunks:
        chunks.append(current)
    return chunks


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
            if resp.status_code >= 400:
                # Sin esto, un 400 solo deja "400 Bad Request" en el log, sin
                # el campo "description" de Telegram que dice la razón real
                # (ej. "message is too long" vs. "can't parse entities: ...").
                # Truncado por si acaso, para no inflar el log con un cuerpo
                # gigante en un caso raro.
                logger.warning("Respuesta %s de Telegram sendMessage: %s", resp.status_code, resp.text[:500])
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
            if resp.status_code >= 400:
                logger.warning("Respuesta %s de Telegram sendPhoto: %s", resp.status_code, resp.text[:500])
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
            f"{_esc(vb.event.label())}\n"
            f"Inicia: {vb.event.commence_time.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Apuesta: <b>{_esc(vb.description())}</b>\n"
            f"Casa: <b>{_esc(vb.bookmaker)}</b> @ {vb.offered_odds:.2f}\n"
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

    def send_daily_picks_message(
        self, pick_date_str: str, picks: List[ValueBet], bookmaker_links: Optional[dict] = None
    ) -> bool:
        if not picks:
            return self.send(
                f"🎯 <b>Pronósticos del {pick_date_str}</b>\n\n"
                f"No se encontraron oportunidades con el EV mínimo configurado hoy."
            )
        # No se asume "fútbol": 'sports' puede incluir más de un deporte (ver
        # config.example.yaml, leagues_by_sport) y este mensaje debe seguir
        # siendo correcto sin importar cuáles picks entraron hoy.
        bookmaker_links = bookmaker_links or {}
        pick_blocks = []
        for i, vb in enumerate(picks, start=1):
            link = bookmaker_links.get(vb.bookmaker)
            # Enlace opcional: solo aparece si TÚ lo configuraste con tu propia
            # URL verificada (odds_provider.bookmaker_links) — nunca se adivina
            # ni se genera automáticamente, ver la nota de seguridad en el README.
            casa = f'<a href="{html.escape(link, quote=True)}">{_esc(vb.bookmaker)}</a>' if link else _esc(vb.bookmaker)
            pick_lines = [
                f"{i}. <b>{_esc(vb.event.label())}</b>",
                f"   {_esc(vb.description())} @ {vb.offered_odds:.2f} ({casa})",
                f"   EV: <b>+{vb.ev_pct:.1f}%</b>",
            ]
            # Señales secundarias (ver secondary_signals.py) — puramente
            # informativas, no afectan el EV de arriba. Solo aparecen si
            # están activadas Y se pudo emparejar el partido/equipo por
            # nombre contra PlayerElo/API-Football (ver team_match.py).
            if vb.playerelo_note:
                pick_lines.append(f"   🔎 {_esc(vb.playerelo_note)}")
            if vb.injury_notes:
                for note in vb.injury_notes:
                    pick_lines.append(f"   🩹 {_esc(note)}")
            pick_blocks.append("\n".join(pick_lines))

        footer = (
            "\nAnálisis estadístico automatizado — no coloca apuestas por ti. "
            "Verifica la cuota vigente antes de decidir. Juega con responsabilidad."
        )

        # INCIDENTE REAL de producción (2026-09-12): el primer día con 10
        # picks reales de golpe, Telegram rechazó el mensaje con 400 Bad
        # Request (ver `_esc()` arriba sobre por qué no se pudo confirmar la
        # causa exacta de ESE incidente). El límite de 4096 caracteres de
        # sendMessage es un riesgo real e independiente que también hay que
        # cubrir — nunca se había topado antes porque nunca había pasado de
        # ~4 picks reales por día.
        # Si no cabe en un solo mensaje, se manda en varias partes, cada una
        # con su propio encabezado "(parte N/M)", sin cortar un pick a la
        # mitad entre dos mensajes.
        chunks = _chunk_pick_blocks(pick_blocks, _TELEGRAM_SAFE_MESSAGE_LENGTH - len(footer))
        total_parts = len(chunks)
        ok = True
        for part_num, chunk_blocks in enumerate(chunks, start=1):
            part_suffix = f" (parte {part_num}/{total_parts})" if total_parts > 1 else ""
            lines = [f"🎯 <b>Pronósticos del día — {pick_date_str}</b>{part_suffix}"]
            if part_num == 1:
                lines.append(f"{len(picks)} picks\n")
            lines.extend(chunk_blocks)
            if part_num == total_parts:
                lines.append(footer)
            ok = self.send("\n".join(lines)) and ok
        return ok

    def send_daily_results_message(self, pick_date_str: str, settled_rows: list, summary: dict) -> bool:
        if not settled_rows:
            return self.send(
                f"📊 <b>Resultados del {pick_date_str}</b>\n\nNo hubo picks liquidados en esta corrida."
            )
        from ..clv import clv_pct  # import local: evita import circular a nivel de módulo

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
            # CLV por pick (ver clv.py): solo aparece si se alcanzó a capturar
            # la cuota de cierre antes del partido — no siempre va a estar.
            closing = row["closing_odds"] if "closing_odds" in row.keys() else None
            clv_txt = f" · CLV {clv_pct(row['offered_odds'], closing):+.1f}%" if closing else ""
            lines.append(f"{mark} {_esc(row['event_label'])} — {_esc(desc)} @ {row['offered_odds']:.2f}{score}{clv_txt}")
        hit_rate = summary.get("hit_rate_pct")
        hit_rate_txt = f"{hit_rate:.0f}%" if hit_rate is not None else "s/d"
        lines.append(
            f"\nAciertos: {summary.get('won', 0)}/{(summary.get('won', 0) or 0) + (summary.get('lost', 0) or 0)} "
            f"({hit_rate_txt}) · EV promedio del día: {summary.get('avg_ev_pct', 0):.1f}%"
        )

        # Ventana móvil (últimos N días, ver Storage.recent_picks_summary) —
        # a diferencia del resumen mensual, no se resetea el día 1 de cada mes,
        # así que da una lectura útil sin importar qué día del mes sea hoy.
        recent = summary.get("recent_window")
        if recent and recent.get("total"):
            r_hit = recent.get("hit_rate_pct")
            r_hit_txt = f"{r_hit:.0f}%" if r_hit is not None else "s/d"
            r_decided = (recent.get("won", 0) or 0) + (recent.get("lost", 0) or 0)
            clv_part = ""
            if recent.get("clv_sample_size"):
                clv_part = f" · CLV: {recent['avg_clv_pct']:+.1f}% ({recent['clv_sample_size']})"
            lines.append(
                f"Últimos {recent['days']} días: {recent.get('won', 0)}/{r_decided} aciertos "
                f"({r_hit_txt}){clv_part}"
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
        clv_line = ""
        if summary.get("clv_sample_size"):
            clv_line = (
                f"CLV promedio: <b>{summary['avg_clv_pct']:+.1f}%</b> "
                f"sobre {summary['clv_sample_size']} picks con cierre capturado\n"
            )
        text = (
            f"{header}\n\n"
            f"{veredicto}\n\n"
            f"Total de picks: {summary.get('total', 0)}\n"
            f"Ganados: {summary.get('won', 0)} · Perdidos: {summary.get('lost', 0)} · "
            f"Anulados: {summary.get('push', 0)}\n"
            f"Tasa de acierto: {hit_rate_txt}\n"
            f"Profit (stake plano de 1 unidad por pick): <b>{profit:+.2f}u</b>\n"
            f"ROI del mes: <b>{roi_txt}</b>\n"
            f"{clv_line}\n"
            f"Este cálculo asume 1 unidad apostada por pick — no es tu resultado "
            f"real de dinero si apostaste montos distintos o no tomaste todos los "
            f"picks. Análisis estadístico, no garantía de resultados futuros."
        )
        return self.send(text)
