import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.models import Event, ValueBet
from valuebet.monthly import (
    build_stat_tiles,
    generate_monthly_summary_if_due,
    is_first_of_month,
    month_label,
    previous_month,
)
from valuebet.storage.db import Storage


def test_month_label():
    assert month_label(2026, 7) == "Julio 2026"
    assert month_label(2026, 1) == "Enero 2026"
    assert month_label(2026, 12) == "Diciembre 2026"


def test_previous_month_regular():
    assert previous_month(date(2026, 8, 1)) == (2026, 7)


def test_previous_month_crosses_year_boundary():
    assert previous_month(date(2026, 1, 1)) == (2025, 12)


def test_is_first_of_month():
    assert is_first_of_month(date(2026, 8, 1)) is True
    assert is_first_of_month(date(2026, 8, 2)) is False
    assert is_first_of_month(date(2026, 8, 31)) is False


def _summary(total=10, won=6, lost=4, push=0, profit_units=2.5, roi_pct=25.0, hit_rate_pct=60.0):
    return {
        "total": total,
        "won": won,
        "lost": lost,
        "push": push,
        "other": 0,
        "avg_ev_pct": 5.0,
        "profit_units": profit_units,
        "roi_pct": roi_pct,
        "hit_rate_pct": hit_rate_pct,
    }


def test_build_stat_tiles_profitable_month_uses_won_color():
    from valuebet.social_image import BADGE_WON

    tiles = build_stat_tiles(_summary(profit_units=5.0, roi_pct=20.0))
    by_label = {t.label: t for t in tiles}
    assert by_label["Total de picks"].value == "10"
    assert by_label["Ganados"].value == "6"
    assert by_label["Perdidos"].value == "4"
    assert by_label["Profit (stake plano)"].value == "+5.0u"
    assert by_label["Profit (stake plano)"].value_color == BADGE_WON


def test_build_stat_tiles_losing_month_uses_lost_color():
    from valuebet.social_image import BADGE_LOST

    tiles = build_stat_tiles(_summary(profit_units=-3.2, roi_pct=-15.0))
    by_label = {t.label: t for t in tiles}
    assert by_label["Profit (stake plano)"].value == "-3.2u"
    assert by_label["Profit (stake plano)"].value_color == BADGE_LOST


def test_build_stat_tiles_handles_no_decided_picks():
    tiles = build_stat_tiles(_summary(total=3, won=0, lost=0, profit_units=0.0, roi_pct=None, hit_rate_pct=None))
    by_label = {t.label: t for t in tiles}
    assert by_label["Tasa de acierto"].value == "s/d"
    assert by_label["ROI del mes"].value == "s/d"


def _make_vb(event_id, home, away, selection, odds) -> ValueBet:
    event = Event(
        event_id=event_id,
        sport="football",
        league="Liga",
        home_team=home,
        away_team=away,
        commence_time=datetime.utcnow(),
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection=selection,
        bookmaker="Betplay",
        offered_odds=odds,
        fair_probability=0.5,
        ev_pct=8.0,
        reference_bookmakers=["Pinnacle"],
    )


def test_monthly_picks_summary_computes_flat_stake_profit():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))

        # 2 ganadas a distinta cuota, 1 perdida, 1 push, todas en julio 2026.
        vb1 = _make_vb("e1", "A", "B", "home", 2.00)
        vb2 = _make_vb("e2", "C", "D", "home", 3.00)
        vb3 = _make_vb("e3", "E", "F", "home", 1.80)
        vb4 = _make_vb("e4", "G", "H", "home", 2.20)
        for vb in (vb1, vb2, vb3, vb4):
            storage.add_daily_pick("2026-07-15", vb)

        picks = {row["event_id"]: row["id"] for row in storage.list_picks_for_date("2026-07-15")}
        storage.settle_daily_pick(picks["e1"], "won", 2, 0)
        storage.settle_daily_pick(picks["e2"], "won", 1, 0)
        storage.settle_daily_pick(picks["e3"], "lost", 0, 1)
        storage.settle_daily_pick(picks["e4"], "push", 1, 1)

        # Un pick de otro mes que NO debe contarse.
        vb_other_month = _make_vb("e5", "I", "J", "home", 5.00)
        storage.add_daily_pick("2026-08-01", vb_other_month)
        other_id = storage.list_picks_for_date("2026-08-01")[0]["id"]
        storage.settle_daily_pick(other_id, "won", 3, 0)

        summary = storage.monthly_picks_summary(2026, 7)
        assert summary["total"] == 4
        assert summary["won"] == 2
        assert summary["lost"] == 1
        assert summary["push"] == 1
        # profit = (2.00-1) + (3.00-1) - 1 = 1 + 2 - 1 = 2.0 (el push no suma ni resta)
        assert summary["profit_units"] == 2.0
        # decided = won+lost = 3 -> roi = 2.0/3*100
        assert round(summary["roi_pct"], 2) == round(2.0 / 3 * 100, 2)
        assert round(summary["hit_rate_pct"], 2) == round(2 / 3 * 100, 2)


def test_monthly_picks_summary_empty_month_returns_none_stats():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        summary = storage.monthly_picks_summary(2026, 6)
        assert summary["total"] == 0
        assert summary["roi_pct"] is None
        assert summary["hit_rate_pct"] is None


class RecordingAlerter:
    def __init__(self):
        self.monthly_calls = []
        self.photo_calls = []

    def send_monthly_summary_message(self, label, summary, is_profitable):
        self.monthly_calls.append((label, summary, is_profitable))
        return True

    def send_photo(self, path, caption=None):
        self.photo_calls.append((path, caption))
        return True


class DummyCfg:
    class _Bankroll:
        pass

    def __init__(self, output_dir, instagram=None):
        self.output_dir = output_dir
        self.instagram = instagram


class DummyInstagramConfig:
    def __init__(self, enabled=True):
        self.enabled = enabled


def test_generate_monthly_summary_if_due_only_fires_on_day_one():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"))
        alerter = RecordingAlerter()

        result = generate_monthly_summary_if_due(cfg, storage, alerter, today=date(2026, 8, 15))
        assert result is None
        assert alerter.monthly_calls == []


def test_generate_monthly_summary_if_due_sends_message_and_image_on_day_one():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        vb = _make_vb("e1", "A", "B", "home", 2.20)
        storage.add_daily_pick("2026-07-20", vb)
        row = storage.list_picks_for_date("2026-07-20")[0]
        storage.settle_daily_pick(row["id"], "won", 2, 0)

        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"))
        alerter = RecordingAlerter()

        result = generate_monthly_summary_if_due(cfg, storage, alerter, today=date(2026, 8, 1))
        assert result is not None
        assert result["total"] == 1
        assert len(alerter.monthly_calls) == 1
        label, summary, is_profitable = alerter.monthly_calls[0]
        assert label == "Julio 2026"
        assert is_profitable is True
        assert len(alerter.photo_calls) == 1
        assert Path(alerter.photo_calls[0][0]).exists()


def test_generate_monthly_summary_if_due_queues_instagram_image_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        vb = _make_vb("e1", "A", "B", "home", 2.20)
        storage.add_daily_pick("2026-07-20", vb)
        row = storage.list_picks_for_date("2026-07-20")[0]
        storage.settle_daily_pick(row["id"], "won", 2, 0)

        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"), instagram=DummyInstagramConfig(enabled=True))
        alerter = RecordingAlerter()
        queue = []

        result = generate_monthly_summary_if_due(cfg, storage, alerter, today=date(2026, 8, 1), instagram_queue=queue)

        assert result is not None
        assert len(queue) == 1
        assert queue[0]["kind"] == "monthly"
        assert queue[0]["path"].endswith(".jpg")
        assert Path(queue[0]["path"]).exists()
        assert "Julio 2026" in queue[0]["caption"]


def test_generate_monthly_summary_if_due_does_not_queue_when_instagram_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        vb = _make_vb("e1", "A", "B", "home", 2.20)
        storage.add_daily_pick("2026-07-20", vb)
        row = storage.list_picks_for_date("2026-07-20")[0]
        storage.settle_daily_pick(row["id"], "won", 2, 0)

        cfg = DummyCfg(output_dir=str(Path(tmp) / "output"))  # instagram=None por defecto
        alerter = RecordingAlerter()
        queue = []

        generate_monthly_summary_if_due(cfg, storage, alerter, today=date(2026, 8, 1), instagram_queue=queue)

        assert queue == []
