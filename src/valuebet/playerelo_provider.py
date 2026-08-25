"""Cliente HTTP para la API de PlayerElo (https://playerelo.football).

IMPORTANTE — ESTADO ACTUAL: SOLO CONECTIVIDAD, TODAVÍA NO HAY PARSEO.

Este proyecto ya se tropezó tres veces (Wplay, Pinnacle, el campo 'hdp' vs
'point' de odds-api.io) por asumir la forma de una respuesta externa sin
verificarla contra una llamada real. Para no repetirlo con una API nueva,
este archivo solo resuelve la autenticación y la conexión (`raw_get`, que
devuelve el JSON crudo tal cual lo manda el servidor) — el parseo de campos
específicos (probabilidades home/draw/away, nombres de equipo, etc.) se
escribe en una segunda pasada, después de correr `scripts/verify_playerelo.py`
con una API key real y revisar la respuesta real.

Auth: Bearer token (`Authorization: Bearer <api_key>`), según
https://playerelo.football/api-access (consultado ago-2026). Plan gratuito:
500 solicitudes/mes, 10/minuto — por eso este proyecto solo la consulta para
los picks que YA pasaron el filtro de EV (~10/día), nunca para los cientos
de partidos candidatos evaluados cada corrida.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class PlayerEloProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://data-api.playerelo.football",
        timeout: int = 15,
        max_retries: int = 3,
    ):
        if not api_key or api_key.startswith("TU_"):
            raise ValueError(
                "Falta configurar 'secondary_signals.playerelo.api_key' en config.yaml. "
                "Consigue una clave gratis en https://playerelo.football/api-access"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})

    def raw_get(self, path: str, params: Optional[dict] = None) -> dict:
        """Devuelve el JSON crudo de la respuesta, sin interpretar su forma.

        Mismo patrón de reintentos que OddsApiIoProvider._get (odds_provider.py):
        no reintenta 4xx (salvo 429), sí reintenta 429/5xx con backoff, y deja
        el cuerpo de la respuesta en el log si hay un error — para diagnosticar
        sin tener que reproducir la llamada a mano.
        """
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params or {}, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("retry-after", 5)) or 5
                    logger.warning("PlayerElo: rate limit alcanzado, esperando %ss (intento %s)", wait, attempt)
                    time.sleep(min(wait, 60))
                    continue
                if resp.status_code >= 400:
                    logger.warning("PlayerElo: respuesta %s de %s: %s", resp.status_code, url, resp.text[:500])
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("PlayerElo: error consultando %s (intento %s/%s): %s", url, attempt, self.max_retries, exc)
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"No se pudo consultar {url} tras {self.max_retries} intentos") from last_exc
