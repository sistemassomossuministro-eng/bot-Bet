"""TelegramAlerter: contenido de los mensajes (sin red real, se mockea send()).

Cubre las mejoras chicas agregadas sobre CLV: link opcional a la casa de
apuestas en cada pick, CLV por pick en el mensaje de resultados, ventana
móvil de 30 días, y CLV en el resumen mensual.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.alerts.telegram_bot import TelegramAlerter, _chunk_pick_blocks
from valuebet.models import Event, ValueBet


def _alerter() -> TelegramAlerter:
    alerter = TelegramAlerter(bot_token="fake-token", chat_id="12345")
    alerter.send = MagicMock(return_value=True)
    return alerter


def _make_vb(bookmaker="Betplay", home_team="Millonarios", away_team="Nacional") -> ValueBet:
    event = Event(
        event_id="e1",
        sport="football",
        league="Primera A",
        home_team=home_team,
        away_team=away_team,
        commence_time=datetime.utcnow() + timedelta(hours=3),
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection="home",
        bookmaker=bookmaker,
        offered_odds=2.10,
        fair_probability=0.5,
        ev_pct=5.0,
        reference_bookmakers=["Bet365"],
    )


def test_daily_picks_message_includes_link_when_configured():
    alerter = _alerter()
    vb = _make_vb("Betplay")

    alerter.send_daily_picks_message("2026-08-23", [vb], bookmaker_links={"Betplay": "https://mi-casa-real/"})

    text = alerter.send.call_args[0][0]
    assert '<a href="https://mi-casa-real/">Betplay</a>' in text


def test_daily_picks_message_omits_link_when_not_configured():
    alerter = _alerter()
    vb = _make_vb("Betplay")

    alerter.send_daily_picks_message("2026-08-23", [vb])  # sin bookmaker_links

    text = alerter.send.call_args[0][0]
    assert "<a href=" not in text
    assert "Betplay" in text


def _row(**overrides) -> dict:
    base = dict(
        result="won",
        home_score=2,
        away_score=0,
        market_key="h2h",
        selection="home",
        home_team="Millonarios",
        away_team="Nacional",
        event_label="Millonarios vs Nacional (Primera A)",
        offered_odds=2.10,
        closing_odds=None,
    )
    base.update(overrides)
    return base


def test_daily_results_message_includes_per_pick_clv_when_captured():
    alerter = _alerter()
    row = _row(closing_odds=1.90)  # offered 2.10 > closing 1.90 -> CLV positivo

    alerter.send_daily_results_message("2026-08-22", [row], summary={"won": 1, "lost": 0, "avg_ev_pct": 5.0})

    text = alerter.send.call_args[0][0]
    assert "CLV +10.5%" in text  # (2.10/1.90 - 1) * 100 ≈ 10.526


def test_daily_results_message_omits_clv_when_not_captured():
    alerter = _alerter()
    row = _row(closing_odds=None)

    alerter.send_daily_results_message("2026-08-22", [row], summary={"won": 1, "lost": 0, "avg_ev_pct": 5.0})

    text = alerter.send.call_args[0][0]
    assert "CLV" not in text


def test_daily_results_message_includes_recent_window_line_when_present():
    alerter = _alerter()
    row = _row()
    summary = {
        "won": 1,
        "lost": 0,
        "avg_ev_pct": 5.0,
        "recent_window": {
            "days": 30,
            "total": 12,
            "won": 7,
            "lost": 5,
            "hit_rate_pct": 58.3,
            "clv_sample_size": 4,
            "avg_clv_pct": 2.7,
        },
    }

    alerter.send_daily_results_message("2026-08-22", [row], summary=summary)

    text = alerter.send.call_args[0][0]
    assert "Últimos 30 días: 7/12 aciertos" in text
    assert "CLV: +2.7% (4)" in text


def test_daily_results_message_omits_recent_window_line_when_empty():
    alerter = _alerter()
    row = _row()
    summary = {"won": 1, "lost": 0, "avg_ev_pct": 5.0, "recent_window": {"days": 30, "total": 0}}

    alerter.send_daily_results_message("2026-08-22", [row], summary=summary)

    text = alerter.send.call_args[0][0]
    assert "Últimos" not in text


def test_monthly_summary_message_includes_clv_when_sample_present():
    alerter = _alerter()
    summary = {
        "total": 10, "won": 6, "lost": 4, "push": 0,
        "hit_rate_pct": 60.0, "roi_pct": 15.0, "profit_units": 1.5,
        "clv_sample_size": 5, "avg_clv_pct": 3.4,
    }

    alerter.send_monthly_summary_message("Agosto 2026", summary, is_profitable=True)

    text = alerter.send.call_args[0][0]
    assert "CLV promedio: <b>+3.4%</b> sobre 5 picks" in text


def test_monthly_summary_message_omits_clv_without_sample():
    alerter = _alerter()
    summary = {
        "total": 10, "won": 6, "lost": 4, "push": 0,
        "hit_rate_pct": 60.0, "roi_pct": 15.0, "profit_units": 1.5,
        "clv_sample_size": 0, "avg_clv_pct": None,
    }

    alerter.send_monthly_summary_message("Agosto 2026", summary, is_profitable=True)

    text = alerter.send.call_args[0][0]
    assert "CLV promedio" not in text


def test_daily_picks_message_escapes_html_special_chars_in_team_names():
    """INCIDENTE REAL (2026-09-12): el primer día con 10 picks reales,
    Telegram rechazó el mensaje con 400 Bad Request. Nunca se había
    verificado que un nombre de equipo/liga real (odds-api.io) no pudiera
    traer '&', '<' o '>' — sin escapar, cualquiera de esos rompe el parser
    HTML de Telegram y tumba el mensaje completo."""
    alerter = _alerter()
    vb = _make_vb(home_team="River & Plate", away_team="Boca <Juniors>")

    alerter.send_daily_picks_message("2026-09-12", [vb])

    text = alerter.send.call_args[0][0]
    assert "River &amp; Plate" in text
    assert "Boca &lt;Juniors&gt;" in text
    # Nunca debe quedar un '&'/'<'/'>' crudo que no sea parte de una etiqueta
    # nuestra (<b>, <a>) o de una entidad ya escapada (&amp;, &lt;, &gt;).
    assert "River & Plate" not in text
    assert "Boca <Juniors>" not in text


def test_daily_picks_message_escapes_secondary_signal_notes():
    alerter = _alerter()
    vb = _make_vb()
    vb.playerelo_note = "PlayerElo: 55% <> Bet365: 50%"
    vb.injury_notes = ["Jugador & Co. (duda)"]

    alerter.send_daily_picks_message("2026-09-12", [vb])

    text = alerter.send.call_args[0][0]
    assert "PlayerElo: 55% &lt;&gt; Bet365: 50%" in text
    assert "Jugador &amp; Co. (duda)" in text


def test_daily_results_message_escapes_html_special_chars_in_team_names():
    alerter = _alerter()
    row = _row(home_team="River & Plate", away_team="Nacional", event_label="River & Plate vs Nacional (Primera A)")

    alerter.send_daily_results_message("2026-08-22", [row], summary={"won": 1, "lost": 0, "avg_ev_pct": 5.0})

    text = alerter.send.call_args[0][0]
    assert "River &amp; Plate" in text
    assert "River & Plate" not in text


def test_chunk_pick_blocks_keeps_each_chunk_under_the_limit_without_splitting_a_block():
    blocks = ["a" * 100 for _ in range(10)]  # 10 bloques de 100 chars c/u

    chunks = _chunk_pick_blocks(blocks, max_chars=250)

    assert all(sum(len(b) + 1 for b in chunk) <= 250 for chunk in chunks)
    # Ningún bloque se parte: la cantidad total de bloques se preserva.
    assert sum(len(chunk) for chunk in chunks) == len(blocks)


def test_chunk_pick_blocks_returns_one_chunk_when_everything_fits():
    blocks = ["corto"] * 3

    chunks = _chunk_pick_blocks(blocks, max_chars=1000)

    assert len(chunks) == 1
    assert chunks[0] == blocks


def test_chunk_pick_blocks_handles_empty_list():
    assert _chunk_pick_blocks([], max_chars=1000) == [[]]


def test_daily_picks_message_splits_into_multiple_parts_when_too_long():
    """INCIDENTE REAL (2026-09-12): con 10 picks reales el mensaje puede
    acercarse (o superar) el límite de 4096 caracteres de sendMessage —
    nunca se había topado antes porque nunca había pasado de ~4 picks reales
    en un solo día."""
    alerter = _alerter()
    # 30 picks con nombres largos — de sobra para forzar más de un chunk
    # con el margen de seguridad de _TELEGRAM_SAFE_MESSAGE_LENGTH (3500).
    picks = [
        _make_vb(
            home_team=f"Equipo Local Numero {i} Con Nombre Bastante Largo",
            away_team=f"Equipo Visitante Numero {i} Tambien Con Nombre Largo",
        )
        for i in range(30)
    ]

    ok = alerter.send_daily_picks_message("2026-09-12", picks)

    assert ok is True
    assert alerter.send.call_count > 1
    all_texts = [call.args[0] for call in alerter.send.call_args_list]
    # Cada parte se anuncia como tal.
    assert all("(parte" in t for t in all_texts)
    # El conteo total de picks solo aparece una vez, en la primera parte.
    assert sum(1 for t in all_texts if "30 picks" in t) == 1
    # El pie de "análisis estadístico" solo aparece en la última parte.
    assert sum(1 for t in all_texts if "Análisis estadístico automatizado" in t) == 1
    assert "Análisis estadístico automatizado" in all_texts[-1]
    # Ninguna parte individual se pasa del límite real de Telegram.
    assert all(len(t) <= 4096 for t in all_texts)


def test_send_logs_telegram_response_body_on_error():
    """Sin esto, un 400 de Telegram solo dejaba '400 Bad Request' en el
    log — sin el campo 'description' real que dice POR QUÉ (mensaje
    demasiado largo, entidades HTML mal formadas, etc.), justo lo que faltó
    para diagnosticar el incidente del 2026-09-12 de una sola vez."""
    alerter = TelegramAlerter(bot_token="fake-token", chat_id="12345")
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.text = '{"ok":false,"description":"Bad Request: message is too long"}'
    fake_resp.raise_for_status.side_effect = __import__("requests").exceptions.HTTPError("400")

    with patch("valuebet.alerts.telegram_bot.requests.post", return_value=fake_resp), \
         patch("valuebet.alerts.telegram_bot.logger") as mock_logger:
        ok = alerter.send("hola")

    assert ok is False
    assert mock_logger.warning.called
    assert "message is too long" in str(mock_logger.warning.call_args)
