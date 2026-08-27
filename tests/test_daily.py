import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import (
    AppConfig,
    DailyConfig,
    OddsProviderConfig,
    PlayerEloConfig,
    SecondarySignalsConfig,
    ValueDetectionConfig,
)
from valuebet.daily import generate_daily_picks, select_daily_picks, settle_pending_daily_picks
from valuebet.kelly import BankrollLimits
from valuebet.models import BookmakerMarket, Event, Outcome, ValueBet
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


class _ProviderThatAlwaysFailsResult:
    """Simula un evento cuyo event_id ya no existe en el proveedor (404
    'Event not found' — visto en producción, ago-2026): get_event_result
    siempre lanza una excepción."""

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        return []

    def get_event_odds(self, event_id, bookmakers):
        raise NotImplementedError

    def get_events_odds(self, event_ids, bookmakers):
        raise NotImplementedError

    def get_event_result(self, event_id):
        raise RuntimeError("404 Event not found")


def test_settle_pending_daily_picks_expires_old_pick_even_if_provider_query_fails():
    """Bug real corregido: antes, si get_event_result fallaba (ej. el evento
    ya no existe en odds-api.io), el pick quedaba pendiente PARA SIEMPRE —
    nunca llegaba a chequear settlement_max_age_days porque el 'continue' del
    except lo saltaba. Ahora debe expirar igual que un pick que simplemente
    nunca recibe resultado."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        old_date = (datetime.utcnow() - timedelta(days=10)).date().isoformat()
        vb = make_vb("evtE", 5.0, "home")
        storage.add_daily_pick(old_date, vb)

        provider = _ProviderThatAlwaysFailsResult()
        cfg = _cfg(settlement_max_age_days=5)
        settle_pending_daily_picks(cfg, provider, storage, today=datetime.utcnow().date())

        row = storage.get_daily_pick(1)
        assert row["result"] == "unsettled_expired"


def test_settle_pending_daily_picks_leaves_recent_pick_pending_when_provider_query_fails():
    """El mismo fallo de consulta, pero un pick reciente (dentro del margen)
    debe seguir pendiente (para reintentar mañana), no expirar de una vez."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
        vb = make_vb("evtF", 5.0, "home")
        storage.add_daily_pick(yesterday, vb)

        provider = _ProviderThatAlwaysFailsResult()
        cfg = _cfg(settlement_max_age_days=5)
        settled = settle_pending_daily_picks(cfg, provider, storage, today=datetime.utcnow().date())

        assert settled == []
        pending = storage.list_pending_picks_before(datetime.utcnow().date().isoformat())
        assert len(pending) == 1


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


class _FakeProviderWithValueBet:
    """Un solo evento h2h con EV positivo real (Betplay vs Pinnacle) — para
    probar que generate_daily_picks no truena si secondary_signals está
    activado pero con una api_key placeholder (ValueError esperado y
    capturado, ver daily.py::_enrich_picks_with_secondary_signals_safely)."""

    def __init__(self):
        self._event = Event(
            event_id="today1",
            sport="football",
            league="Primera A",
            home_team="Millonarios",
            away_team="Nacional",
            commence_time=datetime.utcnow() + timedelta(hours=5),
            bookmakers={
                "Pinnacle": [
                    BookmakerMarket(
                        bookmaker="Pinnacle",
                        market_key="h2h",
                        updated_at=None,
                        outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
                    )
                ],
                "Betplay": [
                    BookmakerMarket(
                        bookmaker="Betplay",
                        market_key="h2h",
                        updated_at=None,
                        outcomes=[Outcome("home", 2.30), Outcome("draw", 3.30), Outcome("away", 3.80)],
                    )
                ],
            },
        )

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        return [self._event]

    def get_events_odds(self, event_ids, bookmakers):
        return [self._event for _ in event_ids]

    def get_event_result(self, event_id):
        raise NotImplementedError


def test_generate_daily_picks_does_not_crash_with_placeholder_secondary_signals_key():
    """secondary_signals.playerelo.enabled=true con una api_key placeholder
    (usuario aún no la configuró) debe registrar un warning y seguir sin esa
    señal — nunca tumbar el resumen diario completo."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        cfg = AppConfig(
            bankroll=BankrollLimits(total=1_000_000),
            odds_provider=OddsProviderConfig(
                name="odds_api_io",
                api_key="x",
                base_url="https://x",
                target_bookmakers=["Betplay"],
                reference_bookmakers=["Pinnacle"],
                sports=["football"],
            ),
            value_detection=ValueDetectionConfig(min_ev_pct=1.0),
            daily=DailyConfig(num_picks=10, max_picks_per_event=1),
            telegram=None,
            db_path="",
            output_dir="output",
            log_level="INFO",
            log_file=None,
            secondary_signals=SecondarySignalsConfig(
                playerelo=PlayerEloConfig(enabled=True, api_key="TU_PLAYERELO_API_KEY_AQUI")
            ),
        )
        provider = _FakeProviderWithValueBet()

        picks = generate_daily_picks(cfg, provider, storage)

        assert len(picks) == 1
        assert picks[0].playerelo_note is None  # nunca se pudo construir el provider real


def test_generate_daily_picks_does_not_repeat_same_event_across_consecutive_runs():
    """Bug real evitado al subir daily.lookahead_days de 1 a 3 (ago-2026): un
    mismo partido lejano puede seguir cumpliendo las reglas de valor varias
    corridas seguidas antes de jugarse — sin dedupe, se recomendaría el mismo
    evento por Telegram dos (o tres) mañanas seguidas. `_FakeProviderWithValueBet`
    siempre devuelve el mismo evento/cuotas, simulando justo ese escenario."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "t.db"))
        cfg = AppConfig(
            bankroll=BankrollLimits(total=1_000_000),
            odds_provider=OddsProviderConfig(
                name="odds_api_io",
                api_key="x",
                base_url="https://x",
                target_bookmakers=["Betplay"],
                reference_bookmakers=["Pinnacle"],
                sports=["football"],
            ),
            value_detection=ValueDetectionConfig(min_ev_pct=1.0),
            daily=DailyConfig(num_picks=10, max_picks_per_event=1, lookahead_days=3),
            telegram=None,
            db_path="",
            output_dir="output",
            log_level="INFO",
            log_file=None,
        )
        provider = _FakeProviderWithValueBet()

        day1_picks = generate_daily_picks(cfg, provider, storage, pick_date=date(2026, 8, 24))
        day2_picks = generate_daily_picks(cfg, provider, storage, pick_date=date(2026, 8, 25))

        assert len(day1_picks) == 1
        assert day2_picks == []  # mismo event_id ya recomendado ayer -> se omite

        # También confirma que no quedó una segunda fila del mismo evento en storage.
        all_event_ids = [
            row["event_id"]
            for pick_date_str in ("2026-08-24", "2026-08-25")
            for row in storage.list_picks_for_date(pick_date_str)
        ]
        assert all_event_ids == ["today1"]
