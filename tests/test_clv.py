"""clv.py: captura de cuota de cierre para medir Closing Line Value.

odds-api.io no da cuotas históricas en el plan gratuito, así que el cierre
se captura en vivo, poco antes del partido, con una corrida aparte (ver
clv_snapshot.yml). Estos tests cubren la lógica de esa captura sin red real.
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from valuebet.clv import capture_closing_snapshots, clv_pct
from valuebet.config import AppConfig, DailyConfig, OddsProviderConfig, ValueDetectionConfig
from valuebet.kelly import BankrollLimits
from valuebet.models import BookmakerMarket, Event, Outcome, ValueBet
from valuebet.storage.db import Storage


def _cfg(clv_window_hours=3.0) -> AppConfig:
    return AppConfig(
        bankroll=BankrollLimits(total=1_000_000),
        odds_provider=OddsProviderConfig(
            name="odds_api_io",
            api_key="x",
            base_url="https://x",
            target_bookmakers=["Betplay"],
            reference_bookmakers=["Bet365"],
            sports=["football"],
        ),
        value_detection=ValueDetectionConfig(),
        daily=DailyConfig(clv_window_hours=clv_window_hours),
        telegram=None,
        db_path="",
        output_dir="output",
        log_level="INFO",
        log_file=None,
    )


def make_vb(event_id, commence_time, selection="home", odds=2.10) -> ValueBet:
    event = Event(
        event_id=event_id,
        sport="football",
        league="Primera A",
        home_team="Millonarios",
        away_team="Nacional",
        commence_time=commence_time,
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection=selection,
        bookmaker="Betplay",
        offered_odds=odds,
        fair_probability=0.5,
        ev_pct=5.0,
        reference_bookmakers=["Bet365"],
    )


class FakeProvider:
    """Proveedor falso que devuelve cuotas "de cierre" fijas para un event_id,
    o nada, según se configure — sin red real."""

    def __init__(self, odds_by_event: dict):
        self.odds_by_event = odds_by_event  # event_id -> {bookmaker: {selection: price}}
        self.calls: list = []  # [(event_ids, bookmakers), ...]

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        raise NotImplementedError

    def get_event_odds(self, event_id, bookmakers):
        raise NotImplementedError

    def get_events_odds(self, event_ids, bookmakers):
        self.calls.append((list(event_ids), list(bookmakers)))
        events = []
        for event_id in event_ids:
            per_bookmaker = self.odds_by_event.get(event_id)
            if not per_bookmaker:
                continue
            bk_markets = {}
            for bookmaker, selections in per_bookmaker.items():
                outcomes = [Outcome(name=sel, price_decimal=price) for sel, price in selections.items()]
                bk_markets[bookmaker] = [
                    BookmakerMarket(bookmaker=bookmaker, market_key="h2h", updated_at=None, outcomes=outcomes)
                ]
            events.append(
                Event(
                    event_id=event_id,
                    sport="football",
                    league="Primera A",
                    home_team="Millonarios",
                    away_team="Nacional",
                    commence_time=datetime.utcnow(),
                    bookmakers=bk_markets,
                )
            )
        return events

    def get_event_result(self, event_id):
        raise NotImplementedError


def test_clv_pct_positive_when_offered_beats_closing():
    # Cuota tomada más alta que la de cierre = valor confirmado (positivo).
    assert clv_pct(2.00, 1.80) == pytest.approx((2.00 / 1.80 - 1) * 100)
    assert clv_pct(2.00, 1.80) > 0


def test_clv_pct_negative_when_market_moved_against_the_pick():
    assert clv_pct(1.80, 2.00) < 0


def test_capture_closing_snapshots_captures_within_window():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        now = datetime(2026, 8, 23, 12, 0, 0)
        vb = make_vb("evt1", commence_time=now + timedelta(hours=1), odds=2.10)
        storage.add_daily_pick("2026-08-23", vb)

        provider = FakeProvider({"evt1": {"Betplay": {"home": 1.85}}})
        cfg = _cfg(clv_window_hours=3.0)

        captured = capture_closing_snapshots(cfg, provider, storage, now=now)

        assert captured == 1
        row = storage.list_picks_for_date("2026-08-23")[0]
        assert row["closing_odds"] == 1.85
        assert row["closing_captured_at"] is not None


def test_capture_closing_snapshots_skips_events_outside_window():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        now = datetime(2026, 8, 23, 12, 0, 0)
        vb = make_vb("evt-far", commence_time=now + timedelta(days=3), odds=2.10)
        storage.add_daily_pick("2026-08-23", vb)

        provider = FakeProvider({"evt-far": {"Betplay": {"home": 1.85}}})
        cfg = _cfg(clv_window_hours=3.0)

        captured = capture_closing_snapshots(cfg, provider, storage, now=now)

        assert captured == 0
        assert provider.calls == []  # ni siquiera debería haber pedido cuotas
        row = storage.list_picks_for_date("2026-08-23")[0]
        assert row["closing_odds"] is None


def test_capture_closing_snapshots_leaves_closing_odds_null_when_market_missing():
    """Si la casa ya cerró ese mercado (o cambió de nombre la selección), no
    debe explotar — simplemente deja closing_odds sin capturar para ese pick."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        now = datetime(2026, 8, 23, 12, 0, 0)
        vb = make_vb("evt1", commence_time=now + timedelta(hours=1), selection="home", odds=2.10)
        storage.add_daily_pick("2026-08-23", vb)

        # El proveedor ya no tiene la selección 'home' para ese partido/casa.
        provider = FakeProvider({"evt1": {"Betplay": {"away": 3.40}}})
        cfg = _cfg(clv_window_hours=3.0)

        captured = capture_closing_snapshots(cfg, provider, storage, now=now)

        assert captured == 0
        row = storage.list_picks_for_date("2026-08-23")[0]
        assert row["closing_odds"] is None


def test_capture_closing_snapshots_uses_cfg_window_when_not_overridden():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        now = datetime(2026, 8, 23, 12, 0, 0)
        # A 2 horas: entra con clv_window_hours=3 (default del cfg), no con 1.
        vb = make_vb("evt1", commence_time=now + timedelta(hours=2), odds=2.10)
        storage.add_daily_pick("2026-08-23", vb)

        provider = FakeProvider({"evt1": {"Betplay": {"home": 1.85}}})
        cfg = _cfg(clv_window_hours=3.0)

        captured = capture_closing_snapshots(cfg, provider, storage, now=now)  # sin window_hours explícito

        assert captured == 1
