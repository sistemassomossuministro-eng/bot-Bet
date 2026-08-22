import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import OddsProviderConfig, leagues_for_sport, load_config

CONFIG_YAML = """
bankroll:
  total: 1000000
odds_provider:
  api_key: "TU_API_KEY_AQUI"
  target_bookmakers: ["Betplay"]
  reference_bookmakers: ["Pinnacle"]
  sports: ["football"]
alerts:
  telegram:
    enabled: false
    bot_token: "TU_BOT_TOKEN_AQUI"
    chat_id: "TU_CHAT_ID_AQUI"
"""


def test_env_vars_override_placeholders(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(CONFIG_YAML)

        monkeypatch.setenv("ODDS_API_KEY", "real-odds-key")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "real-bot-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        cfg = load_config(str(cfg_path))

        assert cfg.odds_provider.api_key == "real-odds-key"
        assert cfg.telegram is not None
        assert cfg.telegram.enabled is True
        assert cfg.telegram.bot_token == "real-bot-token"
        assert cfg.telegram.chat_id == "123456"


def test_value_detection_defaults_are_safe_h2h_only():
    """Sin 'value_detection.allowed_markets' explícito en el yaml, debe quedar
    en ['h2h'] únicamente — totals/spreads no están verificados contra la
    respuesta real del proveedor (ver ValueDetectionConfig)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(CONFIG_YAML)

        cfg = load_config(str(cfg_path))

        assert cfg.value_detection.allowed_markets == ["h2h"]
        assert cfg.value_detection.max_ev_pct == 50.0


def test_value_detection_allowed_markets_and_max_ev_pct_from_yaml():
    config_with_vd = CONFIG_YAML + (
        "value_detection:\n"
        "  allowed_markets: [\"h2h\", \"totals\"]\n"
        "  max_ev_pct: 25.0\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(config_with_vd)

        cfg = load_config(str(cfg_path))

        assert cfg.value_detection.allowed_markets == ["h2h", "totals"]
        assert cfg.value_detection.max_ev_pct == 25.0


def test_without_env_vars_uses_yaml_placeholders(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IG_USER_ID", raising=False)

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(CONFIG_YAML)

        cfg = load_config(str(cfg_path))

        assert cfg.odds_provider.api_key == "TU_API_KEY_AQUI"
        assert cfg.telegram is None  # enabled:false y sin env vars
        assert cfg.instagram is None  # no viene en el yaml de prueba ni por env vars


def test_instagram_env_vars_activate_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(CONFIG_YAML)

        monkeypatch.setenv("IG_ACCESS_TOKEN", "real-ig-token")
        monkeypatch.setenv("IG_USER_ID", "999888")

        cfg = load_config(str(cfg_path))

        assert cfg.instagram is not None
        assert cfg.instagram.enabled is True
        assert cfg.instagram.access_token == "real-ig-token"
        assert cfg.instagram.ig_user_id == "999888"
        assert cfg.instagram.api_version == "v21.0"


def test_instagram_yaml_placeholders_without_env_vars_stay_disabled(monkeypatch):
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IG_USER_ID", raising=False)

    config_with_ig = CONFIG_YAML + (
        "  instagram:\n"
        "    enabled: false\n"
        '    access_token: "TU_ACCESS_TOKEN_DE_INSTAGRAM_AQUI"\n'
        '    ig_user_id: "TU_IG_USER_ID_AQUI"\n'
    )

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(config_with_ig)

        cfg = load_config(str(cfg_path))

        assert cfg.instagram is None


def _op_cfg(**overrides) -> OddsProviderConfig:
    base = dict(
        name="odds_api_io",
        api_key="x",
        base_url="https://x",
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Bet365"],
        sports=["football", "basketball"],
    )
    base.update(overrides)
    return OddsProviderConfig(**base)


def test_leagues_for_sport_falls_back_to_global_list():
    """Sin entrada propia en leagues_by_sport, un deporte usa la lista global
    'leagues' — así fútbol sigue viendo todas sus ligas por defecto."""
    cfg = _op_cfg(leagues=[])
    assert leagues_for_sport(cfg, "football") is None  # [] -> None -> "todas"


def test_leagues_for_sport_per_sport_override_does_not_affect_other_sports():
    """Caso real: restringir basketball a solo la NBA sin romper la cobertura
    mundial de fútbol — usar 'leagues' (lista única y global) para esto
    limitaría también a fútbol, que no tiene ninguna liga llamada 'usa-nba'."""
    cfg = _op_cfg(leagues=[], leagues_by_sport={"basketball": ["usa-nba"]})
    assert leagues_for_sport(cfg, "football") is None  # sigue siendo "todas"
    assert leagues_for_sport(cfg, "basketball") == ["usa-nba"]


def test_leagues_by_sport_parsed_from_yaml():
    # leagues_by_sport debe quedar DENTRO del bloque odds_provider (no al final
    # del yaml como value_detection) para que se parsee como parte de él.
    config_with_sports = CONFIG_YAML.replace(
        'sports: ["football"]',
        'sports: ["football", "basketball"]\n'
        '  leagues_by_sport:\n'
        '    basketball: ["usa-nba"]',
    )
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(config_with_sports)

        cfg = load_config(str(cfg_path))

        assert cfg.odds_provider.sports == ["football", "basketball"]
        assert leagues_for_sport(cfg.odds_provider, "football") is None
        assert leagues_for_sport(cfg.odds_provider, "basketball") == ["usa-nba"]
