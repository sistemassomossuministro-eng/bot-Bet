"""Job liviano y frecuente: captura la cuota de cierre de los picks pendientes
cuyo partido está por arrancar (ver clv.py para el porqué existe esta corrida
aparte del resumen diario). No coloca apuestas, no genera picks nuevos, no
manda mensajes — solo actualiza data/valuebet.db con la cuota de cierre
capturada, para que el resumen mensual pueda calcular CLV.

Uso:
    python -m valuebet.capture_clv_job --config config.yaml
"""
from __future__ import annotations

import argparse
import logging

from .clv import capture_closing_snapshots
from .config import load_config, setup_logging
from .odds_provider import build_provider
from .storage.db import Storage

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Captura cuotas de cierre para medir CLV")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)

    provider = build_provider(
        {
            "name": cfg.odds_provider.name,
            "api_key": cfg.odds_provider.api_key,
            "base_url": cfg.odds_provider.base_url,
        }
    )
    storage = Storage(cfg.db_path)

    captured = capture_closing_snapshots(cfg, provider, storage)
    logger.info("Cuotas de cierre capturadas en esta corrida: %d", captured)


if __name__ == "__main__":
    main()
