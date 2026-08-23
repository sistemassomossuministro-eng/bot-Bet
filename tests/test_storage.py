import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.models import BookmakerMarket, Event, Outcome, ValueBet
from valuebet.storage.db import Storage


def make_value_bet(event_id="evt1", selection="home", odds=2.20) -> ValueBet:
    event = Event(
        event_id=event_id,
        sport="football",
        league="Primera A",
        home_team="Millonarios",
        away_team="Nacional",
        commence_time=datetime.utcnow() + timedelta(days=1),
        bookmakers={},
    )
    return ValueBet(
        event=event,
        market_key="h2h",
        selection=selection,
        bookmaker="Betplay",
        offered_odds=odds,
        fair_probability=0.5,
        ev_pct=10.0,
        reference_bookmakers=["Pinnacle"],
        suggested_stake=15000.0,
    )


def test_upsert_pending_is_new_then_not_new():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        vb = make_value_bet()

        bet_id_1, is_new_1 = storage.upsert_pending(vb)
        assert is_new_1 is True

        # Mismo evento/mercado/selección/casa -> no debería ser "nuevo" la segunda vez,
        # incluso si el EV cambió (se actualiza in-place mientras siga pending).
        vb.ev_pct = 12.5
        bet_id_2, is_new_2 = storage.upsert_pending(vb)
        assert is_new_2 is False
        assert bet_id_1 == bet_id_2

        row = storage.get(bet_id_1)
        assert row["ev_pct"] == 12.5
        # Los nombres de equipo quedan guardados para poder armar descripciones
        # legibles ("Gana Millonarios") sin tener que re-parsear event_label.
        assert row["home_team"] == "Millonarios"
        assert row["away_team"] == "Nacional"


def test_migration_adds_team_columns_to_legacy_db():
    """Simula una base de datos creada con el esquema anterior (sin
    home_team/away_team) y verifica que Storage la migra sola sin explotar."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE value_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                event_label TEXT NOT NULL,
                commence_time TEXT NOT NULL,
                market_key TEXT NOT NULL,
                selection TEXT NOT NULL,
                bookmaker TEXT NOT NULL,
                offered_odds REAL NOT NULL,
                fair_probability REAL NOT NULL,
                ev_pct REAL NOT NULL,
                suggested_stake REAL,
                actual_stake REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                pnl REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(event_id, market_key, selection, bookmaker)
            );
            CREATE TABLE daily_picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_date TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_label TEXT NOT NULL,
                commence_time TEXT NOT NULL,
                market_key TEXT NOT NULL,
                selection TEXT NOT NULL,
                bookmaker TEXT NOT NULL,
                offered_odds REAL NOT NULL,
                fair_probability REAL NOT NULL,
                ev_pct REAL NOT NULL,
                result TEXT NOT NULL DEFAULT 'pending',
                home_score INTEGER,
                away_score INTEGER,
                settled_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(pick_date, event_id, market_key, selection, bookmaker)
            );
            """
        )
        conn.commit()
        conn.close()

        # Storage debe abrir esta DB "vieja" y agregarle las columnas que faltan.
        storage = Storage(db_path)
        vb = make_value_bet()
        bet_id, is_new = storage.upsert_pending(vb)
        assert is_new is True
        row = storage.get(bet_id)
        assert row["home_team"] == "Millonarios"

        storage.add_daily_pick("2026-08-24", vb)
        pick_row = storage.list_picks_for_date("2026-08-24")[0]
        assert pick_row["away_team"] == "Nacional"
        # La migración también debe haber agregado closing_odds/closing_captured_at
        # (columnas nuevas para CLV) sin explotar sobre esta DB "vieja".
        assert pick_row["closing_odds"] is None
        assert pick_row["closing_captured_at"] is None


def test_closing_odds_round_trip_and_window_filter():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        soon = datetime.utcnow() + timedelta(hours=1)
        far = datetime.utcnow() + timedelta(days=3)

        vb_soon = make_value_bet(event_id="evt-soon")
        vb_soon.event.commence_time = soon
        vb_far = make_value_bet(event_id="evt-far")
        vb_far.event.commence_time = far

        storage.add_daily_pick("2026-08-24", vb_soon)
        storage.add_daily_pick("2026-08-24", vb_far)

        deadline = (datetime.utcnow() + timedelta(hours=3)).isoformat()
        needing = storage.list_picks_needing_closing_snapshot(deadline)
        # Solo el partido que arranca dentro de la ventana (1h) debe salir —
        # el que arranca en 3 días queda fuera hasta que se acerque su hora.
        assert [r["event_id"] for r in needing] == ["evt-soon"]

        pick_id = needing[0]["id"]
        storage.set_closing_odds(pick_id, 1.85, datetime.utcnow().isoformat())

        row = storage.get_daily_pick(pick_id)
        assert row["closing_odds"] == 1.85
        assert row["closing_captured_at"] is not None

        # Ya capturado -> no debe volver a salir en una próxima corrida.
        still_needing = storage.list_picks_needing_closing_snapshot(deadline)
        assert [r["event_id"] for r in still_needing] == []


def test_confirm_reject_settle_flow():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        vb = make_value_bet()
        bet_id, _ = storage.upsert_pending(vb)

        storage.confirm(bet_id, actual_stake=20000.0)
        row = storage.get(bet_id)
        assert row["status"] == "confirmed"
        assert row["actual_stake"] == 20000.0

        storage.settle(bet_id, result="won", pnl=18000.0)
        row = storage.get(bet_id)
        assert row["status"] == "settled"
        assert row["result"] == "won"
        assert row["pnl"] == 18000.0

        stats = storage.stats()
        assert stats["settled_count"] == 1
        assert stats["total_pnl"] == 18000.0


def test_reject_flow():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        vb = make_value_bet()
        bet_id, _ = storage.upsert_pending(vb)
        storage.reject(bet_id)
        row = storage.get(bet_id)
        assert row["status"] == "rejected"


def test_settle_after_reject_does_not_double_count_in_pending():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(str(Path(tmp) / "test.db"))
        vb = make_value_bet()
        bet_id, _ = storage.upsert_pending(vb)
        storage.reject(bet_id)

        # Re-detectar la misma oportunidad (mismo unique key) NO debe reabrirla,
        # porque la condición WHERE status='pending' impide el UPDATE cuando ya
        # está rejected. Debe seguir figurando como is_new=False (misma fila),
        # pero su estado permanece 'rejected'.
        vb.ev_pct = 99.0
        _, is_new = storage.upsert_pending(vb)
        assert is_new is False
        row = storage.get(bet_id)
        assert row["status"] == "rejected"
        assert row["ev_pct"] == 10.0  # no se sobreescribió
