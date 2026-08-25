"""Cliente HTTP para API-Football (api-football.com / api-sports.io).

IMPORTANTE — ESTADO ACTUAL: SOLO CONECTIVIDAD, TODAVÍA NO HAY PARSEO.

Misma precaución que playerelo_provider.py: no se escribe parseo de campos
específicos (lesiones, IDs de equipo, etc.) hasta correr
`scripts/verify_api_football.py` con una API key real y confirmar la forma
exacta de la respuesta — este proyecto ya aprendió esa lección por las malas
con odds-api.io (ver odds_provider.py).

Auth: header `x-apisports-key: <api_key>` (si usas la key directa de
api-football.com) — si en cambio te suscribiste vía RapidAPI, la auth es
distinta (`x-rapidapi-key` + `x-rapidapi-host`); ver
`secondary_signals.injuries.via_rapidapi` en config.yaml. Plan gratuito de
api-football.com: 100 solicitudes/día — por eso este proyecto solo la
consultaría para los equipos de los picks que YA pasaron el filtro de EV
(~10 picks/día = máx. 20 equipos), nunca para los cientos de partidos
candidatos evaluados cada corrida.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class ApiFootballProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://v3.football.api-sports.io",
        via_rapidapi: bool = False,
        timeout: int = 15,
        max_retries: int = 3,
    ):
        if not api_key or api_key.startswith("TU_"):
            raise ValueError(
                "Falta configurar 'secondary_signals.injuries.api_key' en config.yaml. "
                "Consigue una clave gratis en https://www.api-football.com "
                "(o vía RapidAPI, ver 'via_rapidapi')."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        if via_rapidapi:
            # Mismo servicio, distinta puerta de entrada: si te suscribiste
            # desde RapidAPI en vez de directo en api-football.com, la auth y
            # el host cambian. Ver 'secondary_signals.injuries.via_rapidapi'.
            self._session.headers.update(
                {
                    "x-rapidapi-key": api_key,
                    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
                }
            )
        else:
            self._session.headers.update({"x-apisports-key": api_key})

    def raw_get(self, path: str, params: Optional[dict] = None) -> dict:
        """Devuelve el JSON crudo de la respuesta, sin interpretar su forma.

        Mismo patrón de reintentos que OddsApiIoProvider._get (odds_provider.py)."""
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params or {}, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("retry-after", 5)) or 5
                    logger.warning("API-Football: rate limit alcanzado, esperando %ss (intento %s)", wait, attempt)
                    time.sleep(min(wait, 60))
                    continue
                if resp.status_code >= 400:
                    logger.warning("API-Football: respuesta %s de %s: %s", resp.status_code, url, resp.text[:500])
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("API-Football: error consultando %s (intento %s/%s): %s", url, attempt, self.max_retries, exc)
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"No se pudo consultar {url} tras {self.max_retries} intentos") from last_exc
