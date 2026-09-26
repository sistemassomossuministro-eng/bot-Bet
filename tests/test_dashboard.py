import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.dashboard import build_dashboard_tiles, dashboard_status, generate_daily_dashboard
from valuebet.models import Event, ValueBet
from valuebet.storage.db import Storage


def _make_vb(event_id, odds=2.00) -> ValueBet:
    event = Event(
        event_id=event_id,
        sport="football",
        league="Liga",
        home_team="A",
        away_team="B",
        commence_time=datetime.utcnow(),
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection="home",
        bookmaker="Betplay",
        offered_odds=odds,
        fair_probability=0.5,
        ev_pct=8.0,
        reference_bookmakers=["Pinnacle"],
    )


def _summary(**overrides):
    base = {
        "total": 10, "won": 6, "lost": 4, "push": 0, "pending": 0, "other": 0,
        "avg_ev_pct": 5.0, "profit_units": 2.5, "roi_pct": 25.0, "hit_rate_pct": 60.0,
    }
    base.update(overrides)
    return base


def test_build_dashboard_tiles_includes_pending_tile():
    tiles = build_dashboard_tiles(_summary(pending=3))
    by_label = {t.label: t for t in tiles}
    assert by_label["Pendientes"].value == "3"
    assert by_label["Total de picks"].value == "10"


def test_build_dashboard_tiles_omits_clv_without_sample():
    tiles = build_dashboard_tiles(_summary())
    assert "CLV promedio" not in {t.label for t in tiles}


def test_build_dashboard_tiles_includes_clv_with_sample():
    tiles = build_dashboard_tiles(_summary(clv_sample_size=5, avg_clv_pct=2.1))
    by_label = {t.label: t for t in tiles}
    assert by_label["CLV promedio"].value == "2.1% (5)"


def test_dashboard_status_neutral_when_nothing_decided_yet():
    # Principios de mes: picks pendientes, ninguno ganado/perdido todavía.
    assert dashboard_status(_summary(won=0, lost=0, profit_units=0.0, pending=4)) == "neutral"


def test_dashboard_status_profitable_and_negative():
    assert dashboard_status(_summary(won=6, lost=4, profit_units=2.5)) == "profitable"
    assert dashboard_status(_summary(won=2, lost=8, profit_units=-3.0)) == "negative"


def test_generate_daily_dashboard_computes_summary_for_current_month_only():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))

        # Un pick de agosto (mes anterior) que NO debe contarse en el
        # acumulado de septiembre.
        storage.add_daily_pick("2026-08-30", _make_vb("e-august"))
        row = storage.list_picks_for_date("2026-08-30")[0]
        storage.settle_daily_pick(row["id"], "won", 2, 0)

        storage.add_daily_pick("2026-09-01", _make_vb("e-sep1", odds=2.00))
        row = storage.list_picks_for_date("2026-09-01")[0]
        storage.settle_daily_pick(row["id"], "won", 2, 0)
        storage.add_daily_pick("2026-09-02", _make_vb("e-sep2"))  # queda pendiente

        class DummyCfg:
            def __init__(self, output_dir):
                self.output_dir = output_dir

        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"))

        summary = generate_daily_dashboard(cfg, storage, alerter=None, today=date(2026, 9, 2))

        assert summary["total"] == 2
        assert summary["won"] == 1
        assert summary["pending"] == 1
        assert Path(cfg.output_dir, "latest_dashboard.png").exists()


class RecordingAlerter:
    def __init__(self):
        self.dashboard_calls = []
        self.photo_calls = []

    def send_daily_dashboard_message(self, month_label_str, today, summary, status):
        self.dashboard_calls.append((month_label_str, today, summary, status))
        return True

    def send_photo(self, path, caption=None):
        self.photo_calls.append((path, caption))
        return True


def test_generate_daily_dashboard_sends_message_and_photo_every_day_not_just_day_one():
    """A diferencia de generate_monthly_summary_if_due, esto debe disparar
    cualquier día del mes, no solo el 1."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))

        class DummyCfg:
            def __init__(self, output_dir):
                self.output_dir = output_dir

        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"))
        alerter = RecordingAlerter()

        generate_daily_dashboard(cfg, storage, alerter, today=date(2026, 9, 15))

        assert len(alerter.dashboard_calls) == 1
        label, today, summary, status = alerter.dashboard_calls[0]
        assert label == "Septiembre 2026"
        assert today == date(2026, 9, 15)
        assert status == "neutral"  # sin picks todavía este mes
        assert len(alerter.photo_calls) == 1
        assert Path(alerter.photo_calls[0][0]).exists()
