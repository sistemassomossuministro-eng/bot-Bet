import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import InjuriesConfig, PlayerEloConfig, SecondarySignalsConfig
from valuebet.models import Event, ValueBet
from valuebet.secondary_signals import enrich_picks_with_secondary_signals


class _FakeCfg:
    """Solo necesita el atributo que enrich_picks_with_secondary_signals lee —
    evita construir un AppConfig completo en cada test."""

    def __init__(self, playerelo_enabled=False, injuries_enabled=False):
        self.secondary_signals = SecondarySignalsConfig(
            playerelo=PlayerEloConfig(enabled=playerelo_enabled, api_key="pe-key"),
            injuries=InjuriesConfig(enabled=injuries_enabled, api_key="af-key"),
        )


def _make_pick(market_key="h2h", selection="home", home="Millonarios", away="Nacional", day="2026-08-25") -> ValueBet:
    event = Event(
        event_id="evt1",
        sport="football",
        league="Primera A",
        home_team=home,
        away_team=away,
        commence_time=datetime.fromisoformat(f"{day}T20:00:00"),
    )
    return ValueBet(
        event=event,
        market_key=market_key,
        selection=selection,
        bookmaker="Betplay",
        offered_odds=2.20,
        fair_probability=0.40,
        ev_pct=5.0,
        reference_bookmakers=["Bet365"],
    )


def test_no_providers_leaves_picks_untouched():
    picks = [_make_pick()]
    cfg = _FakeCfg(playerelo_enabled=True, injuries_enabled=True)  # activado pero sin provider real
    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=None, injuries_provider=None)
    assert result[0].playerelo_note is None
    assert result[0].injury_notes is None


def test_disabled_in_config_skips_even_with_provider():
    picks = [_make_pick()]
    cfg = _FakeCfg(playerelo_enabled=False, injuries_enabled=False)
    playerelo = MagicMock()
    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=playerelo, injuries_provider=None)
    playerelo.get_predictions_for_date.assert_not_called()
    assert result[0].playerelo_note is None


def test_playerelo_note_set_for_h2h_match():
    picks = [_make_pick(market_key="h2h", selection="home")]
    cfg = _FakeCfg(playerelo_enabled=True)
    playerelo = MagicMock()
    playerelo.get_predictions_for_date.return_value = [{"home_team": "Millonarios", "away_team": "Nacional", "p_home": 0.55}]
    playerelo.find_prediction.return_value = {"home_team": "Millonarios", "away_team": "Nacional", "p_home": 0.55}

    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=playerelo, injuries_provider=None)

    assert result[0].playerelo_note is not None
    assert "55.0%" in result[0].playerelo_note
    assert "40.0%" in result[0].playerelo_note  # fair_probability del pick


def test_playerelo_skipped_for_non_h2h_market():
    """PlayerElo solo da p_home/p_draw/p_away — no aplica a totals/btts en esta fase."""
    picks = [_make_pick(market_key="totals", selection="over_2.5")]
    cfg = _FakeCfg(playerelo_enabled=True)
    playerelo = MagicMock()

    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=playerelo, injuries_provider=None)

    playerelo.get_predictions_for_date.assert_not_called()
    assert result[0].playerelo_note is None


def test_predictions_cached_per_date_not_per_pick():
    """Dos picks del mismo día no deben disparar dos llamadas a la API."""
    picks = [
        _make_pick(home="Millonarios", away="Nacional", day="2026-08-25"),
        _make_pick(home="America", away="Junior", day="2026-08-25"),
    ]
    cfg = _FakeCfg(playerelo_enabled=True)
    playerelo = MagicMock()
    playerelo.get_predictions_for_date.return_value = []
    playerelo.find_prediction.return_value = None

    enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=playerelo, injuries_provider=None)

    assert playerelo.get_predictions_for_date.call_count == 1


def test_injury_notes_set_when_found():
    picks = [_make_pick(home="Millonarios", away="Nacional")]
    cfg = _FakeCfg(injuries_enabled=True)
    injuries = MagicMock()
    injuries.get_injuries_for_date.return_value = [{"team": {"name": "Millonarios"}}]
    injuries.injuries_for_team.side_effect = lambda data, team: ["Jugador X (Lesión)"] if team == "Millonarios" else []

    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=None, injuries_provider=injuries)

    assert result[0].injury_notes == ["Millonarios: Jugador X (Lesión)"]


def test_provider_exception_does_not_crash_and_leaves_note_none():
    picks = [_make_pick()]
    cfg = _FakeCfg(playerelo_enabled=True)
    playerelo = MagicMock()
    playerelo.get_predictions_for_date.side_effect = RuntimeError("API caída")
    playerelo.find_prediction.return_value = None

    result = enrich_picks_with_secondary_signals(picks, cfg, playerelo_provider=playerelo, injuries_provider=None)

    assert result[0].playerelo_note is None
