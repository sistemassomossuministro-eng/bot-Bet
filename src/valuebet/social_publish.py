"""Puente entre las imágenes que ya se generan para Telegram y la cola de
publicaciones pendientes para Instagram.

Instagram exige (Content Publishing API): (a) la imagen en formato JPEG, y
(b) una URL pública para descargarla. Este proyecto no monta un servidor
propio (tiene que seguir siendo 100% gratis) — usa el propio repo de GitHub
como hosting: convierte cada pieza a JPEG y la deja junto al PNG (mismo
commit/push que ya hace el workflow), y la publicación real arma la URL con
raw.githubusercontent.com apuntando a ESE commit, después de que el push ya
ocurrió.

Por eso la publicación no pasa dentro de daily_job.py (que corre ANTES del
push) — daily_job.py y monthly.py solo ENCOLAN qué se debe publicar (esta
cola vive en memoria durante la corrida y se vuelca una sola vez, al final,
a un manifiesto JSON committeado junto con las imágenes). El que de verdad
llama a la API de Instagram es scripts/publish_instagram.py, que corre DESPUÉS
del push, en un paso aparte del workflow.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from PIL import Image

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "instagram_queue.json"


def to_jpeg(png_path: str, quality: int = 90) -> str:
    """Convierte un PNG (nuestras piezas son siempre RGB opacas, sin
    transparencia) a JPEG junto al original. Instagram no acepta PNG."""
    jpg_path = str(Path(png_path).with_suffix(".jpg"))
    img = Image.open(png_path).convert("RGB")
    img.save(jpg_path, "JPEG", quality=quality, optimize=True)
    return jpg_path


def queue_instagram_image(cfg, queue: Optional[list], kind: str, png_path: str, caption: str) -> None:
    """Agrega una imagen a la cola de publicación de Instagram de esta
    corrida. No hace nada (silenciosamente) si Instagram no está
    configurado/activo en cfg, o si no se pasó una lista de cola — esto
    último para que llamadores que no usan Instagram (p.ej. tests viejos)
    no necesiten cambiar nada."""
    if queue is None or not (getattr(cfg, "instagram", None) and cfg.instagram.enabled):
        return
    try:
        jpg_path = to_jpeg(png_path)
    except Exception:
        logger.exception("No se pudo convertir a JPEG para Instagram: %s", png_path)
        return
    queue.append({"kind": kind, "path": jpg_path, "caption": caption})


def write_manifest(output_dir: str, entries: List[dict]) -> str:
    manifest_path = Path(output_dir) / MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
    return str(manifest_path)


def read_manifest(output_dir: str) -> List[dict]:
    manifest_path = Path(output_dir) / MANIFEST_FILENAME
    if not manifest_path.exists():
        return []
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        logger.exception("Manifiesto de Instagram inválido en %s; se ignora.", manifest_path)
        return []
