"""Prueba de integración end-to-end del job diario, sin red ni Telegram:
liquidar picks de ayer (con un proveedor falso) + generar picks de hoy +
generar las imágenes — todo debe correr sin lanzar excepciones."""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import AppConfig, DailyConfig, InstagramConfig, OddsProviderConfig, ValueDetectionConfig
from valuebet.daily import bogota_today
from valuebet.daily_job import run_daily_job
from valuebet.kelly import BankrollLimits
from valuebet.models import BookmakerMarket, Event, Outcome
from valuebet.odds_provider import EventResult
from valuebet.storage.db import Storage


class FakeProviderFull:
    def __init__(self):
        self._today_event = Event(
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
        self._yesterday_result = EventResult("yest1", "settled", home_score=2, away_score=0)

    def list_events(self, sport, leagues=None, lookahead_days=3, limit=None):
        return [self._today_event]

    def get_event_odds(self, event_id, bookmakers):
        return self._today_event

    def get_events_odds(self, event_ids, bookmakers):
        return [self._today_event for _ in event_ids]

    def get_event_result(self, event_id):
        if event_id == "yest1":
            return self._yesterday_result
        return EventResult(event_id, "pending", None, None)


class RecordingAlerter:
    """Alerter falso que solo registra con qué argumentos se le llamó, para
    verificar que run_daily_job pasa bookmaker_links y que el resumen de
    resultados trae la ventana móvil de 30 días (ver clv.py)."""

    def __init__(self):
        self.picks_calls = []
        self.results_calls = []

    def send_daily_picks_message(self, pick_date_str, picks, bookmaker_links=None):
        self.picks_calls.append((pick_date_str, picks, bookmaker_links))
        return True

    def send_daily_results_message(self, pick_date_str, settled_rows, summary):
        self.results_calls.append((pick_date_str, settled_rows, summary))
        return True

    def send_photo(self, path, caption=None):
        return True


def test_run_daily_job_passes_bookmaker_links_and_recent_window():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "t.db")
        output_dir = str(Path(tmp) / "output")
        storage = Storage(db_path)

        from valuebet.models import ValueBet

        yesterday = (bogota_today() - timedelta(days=1)).isoformat()
        yesterday_event = Event(
            event_id="yest1",
            sport="football",
            league="Primera A",
            home_team="Junior",
            away_team="America",
            commence_time=datetime.utcnow() - timedelta(hours=20),
            bookmakers={},
        )
        vb_yesterday = ValueBet(
            event=yesterday_event,
            market_key="h2h",
            selection="home",
            bookmaker="Betplay",
            offered_odds=2.10,
            fair_probability=0.55,
            ev_pct=15.5,
            reference_bookmakers=["Pinnacle"],
        )
        storage.add_daily_pick(yesterday, vb_yesterday)

        cfg = AppConfig(
            bankroll=BankrollLimits(total=1_000_000),
            odds_provider=OddsProviderConfig(
                name="odds_api_io",
                api_key="x",
                base_url="https://x",
                target_bookmakers=["Betplay"],
                reference_bookmakers=["Pinnacle"],
                sports=["football"],
                bookmaker_links={"Betplay": "https://mi-casa-real/"},
            ),
            value_detection=ValueDetectionConfig(min_ev_pct=1.0),
            daily=DailyConfig(num_picks=10, max_picks_per_event=1),
            telegram=None,
            db_path=db_path,
            output_dir=output_dir,
            log_level="INFO",
            log_file=None,
        )

        provider = FakeProviderFull()
        alerter = RecordingAlerter()

        run_daily_job(cfg, provider, storage, alerter=alerter)

        assert len(alerter.picks_calls) == 1
        _, _, bookmaker_links = alerter.picks_calls[0]
        assert bookmaker_links == {"Betplay": "https://mi-casa-real/"}

        assert len(alerter.results_calls) == 1
        _, _, summary = alerter.results_calls[0]
        assert "recent_window" in summary
        assert summary["recent_window"]["days"] == 30


def test_run_daily_job_end_to_end_without_telegram():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "t.db")
        output_dir = str(Path(tmp) / "output")

        storage = Storage(db_path)

        # Sembramos un pick "de ayer" pendiente para que la liquidación tenga algo que hacer.
        from valuebet.models import ValueBet

        # bogota_today() (no datetime.utcnow() a secas) porque la liquidación
        # usa "hoy en hora Bogotá" (UTC-5) como referencia. Entre las 00:00 y
        # las 05:00 UTC, "ayer" en UTC cae el mismo día que "hoy" en Bogotá —
        # usar utcnow() acá hacía que este test fuera flaky en esa ventana.
        yesterday = (bogota_today() - timedelta(days=1)).isoformat()
        yesterday_event = Event(
            event_id="yest1",
            sport="football",
            league="Primera A",
            home_team="Junior",
            away_team="America",
            commence_time=datetime.utcnow() - timedelta(hours=20),
            bookmakers={},
        )
        vb_yesterday = ValueBet(
            event=yesterday_event,
            market_key="h2h",
            selection="home",
            bookmaker="Betplay",
            offered_odds=2.10,
            fair_probability=0.55,
            ev_pct=15.5,
            reference_bookmakers=["Pinnacle"],
        )
        storage.add_daily_pick(yesterday, vb_yesterday)

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
            db_path=db_path,
            output_dir=output_dir,
            log_level="INFO",
            log_file=None,
        )

        provider = FakeProviderFull()

        # No debe lanzar excepciones sin Telegram configurado (alerter=None).
        run_daily_job(cfg, provider, storage, alerter=None)

        # El pick de ayer debe haber quedado liquidado como 'won' (2-0, selección 'home').
        row = storage.get_daily_pick(1)
        assert row["result"] == "won"

        # Debe haberse generado al menos un pick para hoy (Betplay ofrece valor sobre Pinnacle).
        today_str = datetime.utcnow().date().isoformat()
        # Nota: bogota_today() puede diferir del UTC date en algunas horas del día;
        # comprobamos contra cualquier fecha con filas en vez de asumir today_str exacto.
        with storage._conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM daily_picks WHERE pick_date != ?", (yesterday,)).fetchone()
        assert count["c"] >= 1

        # Las imágenes deben haberse generado en disco.
        assert (Path(output_dir) / "latest_results.png").exists()
        assert (Path(output_dir) / "latest_picks.png").exists()

        # Instagram no estaba configurado (telegram/instagram=None): no debe
        # haberse escrito ningún manifiesto de publicación.
        assert not (Path(output_dir) / "instagram_queue.json").exists()


def test_run_daily_job_queues_instagram_images_when_configured():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "t.db")
        output_dir = str(Path(tmp) / "output")
        storage = Storage(db_path)

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
            instagram=InstagramConfig(enabled=True, access_token="tok", ig_user_id="123"),
            db_path=db_path,
            output_dir=output_dir,
            log_level="INFO",
            log_file=None,
        )

        provider = FakeProviderFull()
        run_daily_job(cfg, provider, storage, alerter=None)

        manifest_path = Path(output_dir) / "instagram_queue.json"
        assert manifest_path.exists()
        import json

        entries = json.loads(manifest_path.read_text())
        kinds = {e["kind"] for e in entries}
        assert "picks" in kinds  # el proveedor falso genera valor -> hay picks hoy
        for entry in entries:
            assert entry["path"].endswith(".jpg")
            assert Path(entry["path"]).exists()
            assert entry["caption"]
