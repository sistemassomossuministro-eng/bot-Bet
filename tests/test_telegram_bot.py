"""TelegramAlerter: contenido de los mensajes (sin red real, se mockea send()).

Cubre las mejoras chicas agregadas sobre CLV: link opcional a la casa de
apuestas en cada pick, CLV por pick en el mensaje de resultados, ventana
móvil de 30 días, y CLV en el resumen mensual.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.alerts.telegram_bot import TelegramAlerter
from valuebet.models import Event, ValueBet


def _alerter() -> TelegramAlerter:
    alerter = TelegramAlerter(bot_token="fake-token", chat_id="12345")
    alerter.send = MagicMock(return_value=True)
    return alerter


def _make_vb(bookmaker="Betplay") -> ValueBet:
    event = Event(
        event_id="e1",
        sport="football",
        league="Primera A",
        home_team="Millonarios",
        away_team="Nacional",
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
