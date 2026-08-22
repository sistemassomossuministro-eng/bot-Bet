"""Publicación automática en Instagram vía Graph API (Content Publishing).

Sube la imagen del día (picks/resultados) y, el día 1 de cada mes, el resumen
mensual — a la cuenta de Instagram Business/Creator de BotBet. Solo lectura
de datos propios + publicación de contenido; nunca coloca apuestas ni toca
la cuenta de la casa de apuestas ni credenciales de terceros.

Flujo de 2 pasos de la API (ver README, sección Instagram, para el setup
completo: cuenta Business/Creator, Página de Facebook vinculada, App de Meta
y App Review):
  1) POST /<IG_USER_ID>/media         (image_url, caption) -> creation_id
  2) POST /<IG_USER_ID>/media_publish (creation_id)         -> media_id

Requiere que la imagen ya esté publicada en una URL pública — ver
social_publish.py y scripts/publish_instagram.py para cómo se resuelve eso
con raw.githubusercontent.com sin pagar hosting.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v21.0"
GRAPH_BASE = "https://graph.instagram.com"


@dataclass
class InstagramPublisher:
    access_token: str
    ig_user_id: str
    api_version: str = DEFAULT_API_VERSION
    timeout: int = 30

    def _url(self, path: str) -> str:
        return f"{GRAPH_BASE}/{self.api_version}/{path}"

    def publish_image(self, image_url: str, caption: str = "") -> Optional[str]:
        """Publica una imagen ya hospedada en `image_url`. Devuelve el
        media_id publicado, o None si falla — nunca lanza excepción hacia
        quien llama: un fallo de Instagram no debe tumbar el resto del job
        (Telegram ya se mandó antes, y es lo que realmente importa)."""
        try:
            creation_id = self._create_container(image_url, caption)
            if not creation_id:
                return None
            return self._publish_container(creation_id)
        except Exception:
            logger.exception("Fallo publicando en Instagram (image_url=%s).", image_url)
            return None

    def _create_container(self, image_url: str, caption: str) -> Optional[str]:
        resp = requests.post(
            self._url(f"{self.ig_user_id}/media"),
            data={"image_url": image_url, "caption": caption, "access_token": self.access_token},
            timeout=self.timeout,
        )
        data = resp.json()
        if resp.status_code >= 400 or "id" not in data:
            logger.error("Instagram: falló la creación del media container: %s", data)
            return None
        return data["id"]

    def _publish_container(self, creation_id: str, max_wait_seconds: int = 30) -> Optional[str]:
        # Para imágenes (a diferencia de video) el container casi siempre
        # queda listo de inmediato, pero se revisa el status un par de veces
        # por si acaso — Meta lo recomienda como buena práctica general.
        waited = 0
        status = self._container_status(creation_id)
        while status not in (None, "FINISHED", "ERROR") and waited < max_wait_seconds:
            time.sleep(3)
            waited += 3
            status = self._container_status(creation_id)

        if status == "ERROR":
            logger.error("Instagram: el media container terminó en estado ERROR (creation_id=%s).", creation_id)
            return None

        resp = requests.post(
            self._url(f"{self.ig_user_id}/media_publish"),
            data={"creation_id": creation_id, "access_token": self.access_token},
            timeout=self.timeout,
        )
        data = resp.json()
        if resp.status_code >= 400 or "id" not in data:
            logger.error("Instagram: falló media_publish: %s", data)
            return None
        logger.info("Publicado en Instagram: media_id=%s", data["id"])
        return data["id"]

    def _container_status(self, creation_id: str) -> Optional[str]:
        try:
            resp = requests.get(
                self._url(creation_id),
                params={"fields": "status_code", "access_token": self.access_token},
                timeout=self.timeout,
            )
            data = resp.json()
            return data.get("status_code")
        except Exception:
            return None
