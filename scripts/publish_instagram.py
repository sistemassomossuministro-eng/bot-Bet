"""Segundo paso de la publicación en Instagram — corre DESPUÉS del commit y
push del workflow diario (ver .github/workflows/daily.yml), porque necesita
que las imágenes ya estén disponibles en una URL pública real.

Lee el manifiesto que escribió daily_job.py / monthly.py (la cola de
imágenes JPEG generadas en la corrida de hoy), arma la URL pública de cada
una sobre raw.githubusercontent.com apuntando al commit recién pusheado
(variable de entorno PUSHED_SHA, seteada por el propio workflow), y publica
en Instagram con la Graph API (ver src/valuebet/instagram.py).

Requiere que el repositorio sea PÚBLICO — raw.githubusercontent.com solo
sirve archivos de repos públicos. Ver el README, sección Instagram, para el
resto de prerrequisitos (cuenta Business/Creator, App de Meta, App Review).

Se puede correr a mano para probar: PUSHED_SHA=<sha> python scripts/publish_instagram.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.config import load_config, setup_logging  # noqa: E402
from valuebet.instagram import InstagramPublisher  # noqa: E402
from valuebet.social_publish import read_manifest  # noqa: E402

logger = logging.getLogger(__name__)


def _raw_url(repo: str, ref: str, relative_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{relative_path}"


def main() -> None:
    cfg = load_config(os.environ.get("VALUEBET_CONFIG", "config.yaml"))
    setup_logging(cfg)

    if not (cfg.instagram and cfg.instagram.enabled):
        logger.info("Instagram no está configurado — nada que publicar.")
        return

    entries = read_manifest(cfg.output_dir)
    if not entries:
        logger.info("No hay imágenes en cola para publicar en Instagram hoy.")
        return

    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("PUSHED_SHA") or os.environ.get("GITHUB_REF_NAME") or "main"
    if not repo:
        logger.error(
            "GITHUB_REPOSITORY no está definido — no se puede armar la URL pública de las imágenes. "
            "Este script está pensado para correr dentro de GitHub Actions."
        )
        return

    publisher = InstagramPublisher(
        access_token=cfg.instagram.access_token,
        ig_user_id=cfg.instagram.ig_user_id,
        api_version=cfg.instagram.api_version,
    )

    ok, failed = 0, 0
    for entry in entries:
        image_url = _raw_url(repo, ref, entry["path"])
        logger.info("Publicando en Instagram (%s): %s", entry["kind"], image_url)
        media_id = publisher.publish_image(image_url, caption=entry.get("caption", ""))
        if media_id:
            logger.info("OK — %s publicado, media_id=%s", entry["kind"], media_id)
            ok += 1
        else:
            logger.error("Falló la publicación de '%s' en Instagram (ver el log de arriba).", entry["kind"])
            failed += 1

    logger.info("Publicación en Instagram terminada: %d ok, %d fallidas.", ok, failed)


if __name__ == "__main__":
    main()
