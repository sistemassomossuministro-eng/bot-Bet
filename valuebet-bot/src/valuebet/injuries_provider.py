"""Cliente HTTP para API-Football (api-football.com / api-sports.io).

Auth: header `x-apisports-key: <api_key>` (si usas la key directa de
api-football.com) — si en cambio te suscribiste vía RapidAPI, la auth es
distinta (`x-rapidapi-key` + `x-rapidapi-host`); ver
`secondary_signals.injuries.via_rapidapi` en config.yaml. Plan gratuito de
api-football.com: 100 solicitudes/día.

FORMA REAL CONFIRMADA (2026-08-25, con `scripts/verify_api_football.py`
contra la API real):

- `GET /teams?search=<nombre>` -> `response[].team{id, name, code, country,
  founded, national, logo}` + `venue{...}`. Un mismo club puede traer varios
  resultados (filiales, femenino, sub-19/20) — hay que elegir el que
  coincida por nombre exacto (ver `pick_main_team_id`).

- `GET /injuries?team=<id>&season=<YYYY>` -> ⚠️ **BLOQUEADO en el plan
  gratuito para la temporada EN CURSO** — confirmado con una llamada real:
  `season=2026` devolvió `errors.plan`: "Free plans do not have access to
  this season, try from 2022 to 2024." Por eso este proyecto NO usa
  `team`+`season` para lesiones de hoy.

- `GET /injuries?date=YYYY-MM-DD` -> ✅ SÍ funciona para la temporada en
  curso (confirmado: `date=2026-08-25` trajo 72 resultados de partidos de
  hoy, incluyendo Champions League). Este es el único filtro que este
  proyecto usa — trae TODAS las lesiones reportadas para partidos de ese
  día (de cualquier liga), y se filtra por equipo del lado del cliente (ver
  `injuries_for_team`). Cada item: `player{id, name, photo, type, reason}`,
  `team{id, name, logo}`, `fixture{id, timezone, date, timestamp}`,
  `league{id, season, name, country, logo, flag}`.

- `GET /fixtures?team=<id>&next=<n>` -> ⚠️ **BLOQUEADO en el plan gratuito**
  ("Free plans do not have access to the Next parameter") — no usar. No
  hace falta de todos modos: `/injuries?date=` ya trae su propio
  `fixture.id` por entrada.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests

from .team_match import names_match

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

    def get_injuries_for_date(self, date_str: str) -> List[dict]:
        """Todas las lesiones reportadas para partidos de un día (YYYY-MM-DD).

        Deliberadamente por FECHA y no por team+season — season está
        bloqueado en el plan gratuito para la temporada en curso (ver el
        aviso grande al inicio del archivo), pero date sí funciona y además
        es más barato en cuota: una sola llamada cubre todos los partidos
        de ese día, no una por equipo."""
        data = self.raw_get("/injuries", {"date": date_str})
        if isinstance(data, dict) and isinstance(data.get("errors"), dict) and data["errors"]:
            logger.warning("API-Football: /injuries?date=%s devolvió un error de plan: %s", date_str, data["errors"])
            return []
        if isinstance(data, dict):
            return data.get("response", []) or []
        return []

    @staticmethod
    def injuries_for_team(injuries: List[dict], team_name: str) -> List[str]:
        """Filtra la lista de un día a las lesiones de UN equipo (por nombre,
        ver team_match.names_match — emparejamiento ESTRICTO a propósito) y
        las devuelve como notas legibles, ej. 'D. Alaba (Knee Injury)'."""
        notes: List[str] = []
        for item in injuries:
            team_name_in_item = (item.get("team") or {}).get("name")
            if not names_match(team_name_in_item, team_name):
                continue
            player = item.get("player") or {}
            name = player.get("name")
            if not name:
                continue
            reason = player.get("reason") or player.get("type") or "sin detalle"
            notes.append(f"{name} ({reason})")
        return notes
