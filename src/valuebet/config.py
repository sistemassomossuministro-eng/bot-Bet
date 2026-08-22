"""Carga y validación de config.yaml.

Los secretos (api_key de cuotas, bot_token y chat_id de Telegram) se pueden
sobreescribir con variables de entorno — ODDS_API_KEY, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID — que si están presentes SIEMPRE ganan sobre lo que diga
config.yaml. Así config.yaml puede tener placeholders y quedar seguro para
commitear al repo; en GitHub Actions los valores reales viven en los
Secrets del repositorio y llegan aquí como variables de entorno.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .kelly import BankrollLimits


@dataclass
class OddsProviderConfig:
    name: str
    api_key: str
    base_url: str
    target_bookmakers: List[str]
    reference_bookmakers: List[str]
    sports: List[str]
    leagues: List[str] = field(default_factory=list)
    # 'leagues' de arriba es el filtro por defecto, y se aplica igual a TODOS los
    # deportes en 'sports' si un deporte no tiene su propia entrada aquí. Esto
    # importa en cuanto 'sports' deja de tener un solo elemento: por ejemplo,
    # fútbol quiere leagues=[] (todas las ligas del mundo) pero basketball
    # normalmente se quiere restringir a una liga puntual (ej. solo la NBA, no
    # también NCAA/WNBA/ligas menores) — usar 'leagues' para eso rompería
    # fútbol, porque es la MISMA lista para cualquier deporte que no esté acá.
    # Ver leagues_for_sport() más abajo y el ejemplo en config.example.yaml.
    leagues_by_sport: Dict[str, List[str]] = field(default_factory=dict)
    poll_interval_seconds: int = 120
    lookahead_days: int = 3


def leagues_for_sport(cfg: "OddsProviderConfig", sport: str) -> Optional[List[str]]:
    """Ligas a pedir para un deporte puntual: su entrada en 'leagues_by_sport' si
    existe, si no la lista global 'leagues'. [] o {} siempre significa "todas"."""
    return cfg.leagues_by_sport.get(sport, cfg.leagues) or None


@dataclass
class ValueDetectionConfig:
    devig_method: str = "multiplicative"
    min_ev_pct: float = 3.0
    min_reference_books: int = 1
    # Mercados internos permitidos ("h2h" | "totals" | "spreads"). Por defecto
    # SOLO h2h (1X2/moneyline): es el único mercado validado end-to-end contra
    # una respuesta real de odds-api.io. totals/spreads llegaron a producir EV
    # de +700% en producción por un bug de parseo (líneas de distintos puntos
    # de gol colapsando al mismo nombre de resultado "over"/"under" sin punto
    # — ver odds_provider.py y el README, sección "Mercados soportados").
    allowed_markets: List[str] = field(default_factory=lambda: ["h2h"])
    # Red de seguridad final: ningún value bet real y bien calculado debería
    # acercarse a esto — un EV por encima del tope casi siempre es un bug de
    # datos/parseo, no una oportunidad real, así que se descarta en vez de
    # mostrarse como si fuera confiable.
    max_ev_pct: float = 50.0


@dataclass
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str


@dataclass
class InstagramConfig:
    enabled: bool
    access_token: str
    ig_user_id: str
    api_version: str = "v21.0"


@dataclass
class DailyConfig:
    num_picks: int = 10
    max_picks_per_event: int = 1
    timezone: str = "America/Bogota"
    settlement_max_age_days: int = 5
    lookahead_days: int = 1          # ventana de partidos a evaluar (hoy/mañana próximo), a nivel mundial
    max_events_per_run: int = 400    # tope de partidos por corrida, para no agotar la cuota de la API


@dataclass
class AppConfig:
    bankroll: BankrollLimits
    odds_provider: OddsProviderConfig
    value_detection: ValueDetectionConfig
    daily: DailyConfig
    telegram: Optional[TelegramConfig]
    db_path: str
    output_dir: str
    log_level: str
    log_file: Optional[str]
    # Con default para no romper construcciones existentes de AppConfig(...)
    # (tests, código de terceros) que no pasen este argumento explícitamente.
    instagram: Optional[InstagramConfig] = None


def load_config(path: str = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No existe {path}. Copia config.example.yaml a config.yaml y complétalo."
        )
    raw = yaml.safe_load(p.read_text())

    bankroll_raw = raw["bankroll"]
    bankroll = BankrollLimits(
        total=float(bankroll_raw["total"]),
        kelly_fraction=float(bankroll_raw.get("kelly_fraction", 0.25)),
        max_stake_pct=float(bankroll_raw.get("max_stake_pct", 0.02)),
        daily_stake_limit_pct=float(bankroll_raw.get("daily_stake_limit_pct", 0.10)),
        daily_loss_limit_pct=float(bankroll_raw.get("daily_loss_limit_pct", 0.05)),
    )

    op_raw = raw["odds_provider"]
    api_key = os.environ.get("ODDS_API_KEY") or op_raw["api_key"]
    odds_provider = OddsProviderConfig(
        name=op_raw.get("name", "odds_api_io"),
        api_key=api_key,
        base_url=op_raw.get("base_url", "https://api.odds-api.io/v3"),
        target_bookmakers=op_raw["target_bookmakers"],
        reference_bookmakers=op_raw["reference_bookmakers"],
        sports=op_raw["sports"],
        leagues=op_raw.get("leagues", []),
        leagues_by_sport=op_raw.get("leagues_by_sport", {}),
        poll_interval_seconds=int(op_raw.get("poll_interval_seconds", 120)),
        lookahead_days=int(op_raw.get("lookahead_days", 3)),
    )

    vd_raw = raw.get("value_detection", {})
    value_detection = ValueDetectionConfig(
        devig_method=vd_raw.get("devig_method", "multiplicative"),
        min_ev_pct=float(vd_raw.get("min_ev_pct", 3.0)),
        min_reference_books=int(vd_raw.get("min_reference_books", 1)),
        allowed_markets=vd_raw.get("allowed_markets", ["h2h"]),
        max_ev_pct=float(vd_raw.get("max_ev_pct", 50.0)),
    )

    daily_raw = raw.get("daily", {})
    daily = DailyConfig(
        num_picks=int(daily_raw.get("num_picks", 10)),
        max_picks_per_event=int(daily_raw.get("max_picks_per_event", 1)),
        timezone=daily_raw.get("timezone", "America/Bogota"),
        settlement_max_age_days=int(daily_raw.get("settlement_max_age_days", 5)),
        lookahead_days=int(daily_raw.get("lookahead_days", 1)),
        max_events_per_run=int(daily_raw.get("max_events_per_run", 400)),
    )

    tg_raw = raw.get("alerts", {}).get("telegram", {})
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg_raw.get("bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or tg_raw.get("chat_id")
    telegram = None
    # Se activa si el yaml lo marca enabled, o si llegaron credenciales por
    # variable de entorno (típico en GitHub Actions, donde config.yaml puede
    # no traer 'enabled: true' explícito si se generó desde el ejemplo).
    if (tg_raw.get("enabled") or (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))) and bot_token and chat_id:
        telegram = TelegramConfig(
            enabled=True,
            bot_token=bot_token,
            chat_id=str(chat_id),
        )

    ig_raw = raw.get("alerts", {}).get("instagram", {})
    ig_access_token = os.environ.get("IG_ACCESS_TOKEN") or ig_raw.get("access_token")
    ig_user_id = os.environ.get("IG_USER_ID") or ig_raw.get("ig_user_id")
    instagram = None
    # Misma lógica que Telegram: se activa si el yaml lo marca enabled, o si
    # llegaron credenciales por variable de entorno (típico en GitHub
    # Actions, vía los secretos IG_ACCESS_TOKEN / IG_USER_ID del repo).
    if (
        (ig_raw.get("enabled") or (os.environ.get("IG_ACCESS_TOKEN") and os.environ.get("IG_USER_ID")))
        and ig_access_token
        and ig_user_id
    ):
        instagram = InstagramConfig(
            enabled=True,
            access_token=ig_access_token,
            ig_user_id=str(ig_user_id),
            api_version=ig_raw.get("api_version", "v21.0"),
        )

    storage_raw = raw.get("storage", {})
    logging_raw = raw.get("logging", {})

    return AppConfig(
        bankroll=bankroll,
        odds_provider=odds_provider,
        value_detection=value_detection,
        daily=daily,
        telegram=telegram,
        instagram=instagram,
        db_path=storage_raw.get("db_path", "data/valuebet.db"),
        output_dir=storage_raw.get("output_dir", "output"),
        log_level=logging_raw.get("level", "INFO"),
        log_file=logging_raw.get("file"),
    )


def setup_logging(cfg: AppConfig) -> None:
    handlers = [logging.StreamHandler()]
    if cfg.log_file:
        Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(cfg.log_file))
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
