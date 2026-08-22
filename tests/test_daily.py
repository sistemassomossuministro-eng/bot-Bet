import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import AppConfig, DailyConfig, OddsProviderConfig, ValueDetectionConfig
from valuebet.daily import generate_daily_picks, select_daily_picks, settle_pending_daily_picks
from valuebet.kelly import BankrollLimits
from valuebet.models import Event, ValueBet
from valuebet.odds_provider import EventResult
from valuebet.storage.db import Storage


def make_vb(event_id, ev_pct, selection="home") -> ValueBet:
    event = Event(
        event_id=event_id,
        sport="football",
        league="Primera A",
        home_team=f"Local{event_id}",
        away_team=f"Visita{event_id}",
        commence_time=datetime.utcnow() + timedelta(hours=3),
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection=selection,
        bookmaker="Betplay",
        offered_odds=2.10,
        fair_probability=0.5,
        ev_pct=ev_pct,
        reference_bookmakers=["Pinnacle"],
    )


def test_select_daily_picks_diversifies_by_event():
    # 3 oportunidades en el mismo evento, con distinto EV, más otras 2 en eventos distintos.
    candidates = [
        make_vb("evt1", 20.0, "home"),
        make_vb("evt1", 15.0, "draw"),
        make_vb("evt1", 10.0, "away"),
        make_vb("evt2", 12.0),
        make_vb("evt3", 8.0),
    ]
    picks = select_daily_picks(candidates, num_picks=3, max_per_event=1)
    assert len(picks) == 3
    event_ids = [p.event.event_id for p in picks]
    assert len(set(event_ids)) == 3  # diversificado: uno por evento
    assert event_ids[0] == "evt1"  # el de mayor EV de evt1 (20.0) entra primero


def test_select_daily_picks_fills_with_leftovers_if_not_enough_events():
    candidates = [make_vb("evt1", 20.0, "home"), make_vb("evt1", 15.0, "draw")]
    picks = select_daily_picks(candidates, num_picks=2, max_per_event=1)
    # Solo hay 1 evento distinto pero se piden 2 picks -> debe rellenar con el segundo mejor del mismo evento
    assert len(picks) == 2
    assert picks[0].ev_pct == 20.0
    assert picks[1].ev_pct == 15.0


def test_select_daily_picks_returns_fewer_if_not_enough_candidates():
    candidates = [make_vb("evt1", 20.0)]
    picks = select_daily_picks(candidates, num_picks=10, max_per_event=1)
    assert len(picks) == 1


class FakeProvider:
    """Proveedor falso para probar la liquidación sin red."""

    def __init__(self, results: dict):
        self.results = results  # event_id -> EventResult

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        return []

    def get_event_odds(self, event_id, bookmakers):
        raise NotImplementedError

    def get_events_odds(self, event_ids, bookmakers):
        raise NotImplementedError

    def get_event_result(self, event_id):
        return self.results.get(event_id, EventResult(event_id=event_id, status="pending", home_score=None, away_score=None))


def _cfg(settlement_max_age_days=5) -> AppConfig:
    return AppConfig(
        bankroll=BankrollLimits(total=1_000_000),
        odds_provider=OddsProviderConfig(
            name="odds_api_io",
            api_key="x",
            base_url="https://x",
            target_bookmakers=["Betplay"],
            reference_bookmakers=["Pinnacle"],
            sports=["football"],
        ),
        value_detection=ValueDetectionConfig(),
        daily=DailyConfig(num_picks=10, max_picks_per_event=1, settlement_max_age_days=settlement_max_age_days),
        telegram=None,
        db_path="",  # se sobreescribe en cada test con un tmp file
        output_dir="output",
        log_level="INFO",
        log_file=None,
    )


def test_settle_pending_daily_picks_marks_won_and_lost():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()

        vb_win = make_vb("evtA", 10.0, "home")
        vb_lose = make_vb("evtB", 8.0, "home")
        storage.add_daily_pick(yesterday, vb_win)
        storage.add_daily_pick(yesterday, vb_lose)

        provider = FakeProvider(
            {
                "evtA": EventResult("evtA", "settled", 2, 0),  # home gana -> selección 'home' -> won
                "evtB": EventResult("evtB", "settled", 0, 1),  # away gana -> selección 'home' -> lost
            }
        )
        cfg = _cfg()
        settled = settle_pending_daily_picks(cfg, provider, storage, today=datetime.utcnow().date())

        results = {r["event_id"]: r["result"] for r in settled}
        assert results["evtA"] == "won"
        assert results["evtB"] == "lost"


def test_settle_pending_daily_picks_leaves_unfinished_pending():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
        vb = make_vb("evtC", 5.0, "home")
        storage.add_daily_pick(yesterday, vb)

        provider = FakeProvider({"evtC": EventResult("evtC", "pending", None, None)})
        cfg = _cfg()
        settled = settle_pending_daily_picks(cfg, provider, storage, today=datetime.utcnow().date())
        assert settled == []

        pending = storage.list_pending_picks_before(datetime.utcnow().date().isoformat())
        assert len(pending) == 1


def test_settle_pending_daily_picks_expires_old_unsettled():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        old_date = (datetime.utcnow() - timedelta(days=10)).date().isoformat()
        vb = make_vb("evtD", 5.0, "home")
        storage.add_daily_pick(old_date, vb)

        provider = FakeProvider({"evtD": EventResult("evtD", "pending", None, None)})
        cfg = _cfg(settlement_max_age_days=5)
        settle_pending_daily_picks(cfg, provider, storage, today=datetime.utcnow().date())

        row = storage.get_daily_pick(1)
        assert row["result"] == "unsettled_expired"


class RecordingProvider:
    """Proveedor falso que solo registra con qué 'leagues' se llamó a list_events
    por cada deporte, sin devolver eventos reales — para probar que
    leagues_by_sport aísla el filtro de un deporte sin afectar a los demás."""

    def __init__(self):
        self.calls: list = []  # [(sport, leagues), ...]

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        self.calls.append((sport, leagues))
        return []

    def get_event_odds(self, event_id, bookmakers):
        raise NotImplementedError

    def get_events_odds(self, event_ids, bookmakers):
        return []

    def get_event_result(self, event_id):
        raise NotImplementedError


def test_generate_daily_picks_restricts_leagues_per_sport_only():
    """Bug real que este test previene: 'leagues' es una única lista global —
    usarla para restringir basketball a solo la NBA rompería fútbol (que
    quiere TODAS sus ligas). leagues_by_sport debe aislar el filtro."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        cfg = AppConfig(
            bankroll=BankrollLimits(total=1_000_000),
            odds_provider=OddsProviderConfig(
                name="odds_api_io",
                api_key="x",
                base_url="https://x",
                target_bookmakers=["Betplay"],
                reference_bookmakers=["Bet365"],
                sports=["football", "basketball"],
                leagues=[],
                leagues_by_sport={"basketball": ["usa-nba"]},
            ),
            value_detection=ValueDetectionConfig(),
            daily=DailyConfig(num_picks=10, max_picks_per_event=1),
            telegram=None,
            db_path="",
            output_dir="output",
            log_level="INFO",
            log_file=None,
        )
        provider = RecordingProvider()

        generate_daily_picks(cfg, provider, storage)

        calls = dict(provider.calls)
        assert calls["football"] is None  # [] global -> None -> todas las ligas
        assert calls["basketball"] == ["usa-nba"]
