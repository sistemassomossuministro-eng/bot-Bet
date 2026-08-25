"""Cliente HTTP para la API de PlayerElo (https://playerelo.football).

Auth: Bearer token (`Authorization: Bearer <api_key>`), según
https://playerelo.football/api-access (consultado ago-2026). Plan gratuito:
500 solicitudes/mes, 10/minuto — por eso este proyecto solo la consulta para
los picks que YA pasaron el filtro de EV (~10/día), nunca para los cientos
de partidos candidatos evaluados cada corrida. Además se cachea por fecha
dentro de una misma corrida (ver secondary_signals.py) — un mismo `date` no
se pide dos veces aunque varios picks caigan ese día.

FORMA REAL CONFIRMADA (2026-08-25, con `scripts/verify_playerelo.py` contra
la API real — nunca adivinada, ver el aviso en secondary_signals.py sobre
por qué este proyecto siempre verifica primero):

- `GET /v1/predictions?date=YYYY-MM-DD` -> lista de fixtures de ESE día
  (los parámetros `home`/`away` NO filtran nada, se probó y no cambia la
  respuesta — el único filtro real confirmado es `date`; `limit` también
  funciona pero no filtra por equipo). Cada item:
  `fixture_id, kickoff_time (ISO8601 UTC), league_name, league_id,
  home_team, away_team, home_team_elo, away_team_elo, p_home, p_draw,
  p_away, status`. `home_team_elo`/`away_team_elo`/`p_*` pueden salir
  `null` — PlayerElo no tiene rating para esos jugadores/esa liga.
- `GET /v1/fixtures/{id}/prediction` -> mismos campos que un item de arriba
  más `scoreline_distribution` (matriz 8x8 de probabilidades por marcador
  exacto, goles 0-7+ para cada equipo, `null` si no hay elo). Todavía NO se
  usa en este proyecto (solo se confirmó su forma) — queda como posible
  mejora futura para derivar probabilidades de totals/btts, pero no se
  adivina la orientación exacta de la matriz (¿fila=local o visitante?) sin
  un caso más de verificación, así que por ahora solo se usan `p_home`/
  `p_draw`/`p_away` para el mercado h2h.
- `GET /v1/clubs` (liso o con `?search=nombre`) -> equipos rankeados por
  Elo de equipo (no de jugador): `team_id, name, team_elo, league_name,
  league_slug, country, ...`. No se usa todavía (la señal elegida es por
  jugador vía `/v1/predictions`), documentado por si se necesita a futuro.
- `GET /v1/matches/predictions` y `GET /v1/teams` NO EXISTEN (404) — no
  usar, a pesar de sonar plausibles.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests

from .team_match import names_match

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

    def get_predictions_for_date(self, date_str: str) -> List[dict]:
        """Todas las predicciones de PlayerElo para un día (YYYY-MM-DD, UTC).

        `date` es el único filtro real confirmado de /v1/predictions — no
        filtra por equipo, así que hay que traer el día completo y buscar
        el partido que interese (ver find_prediction)."""
        data = self.raw_get("/v1/predictions", {"date": date_str})
        return data if isinstance(data, list) else []

    @staticmethod
    def find_prediction(predictions: List[dict], home_team: str, away_team: str) -> Optional[dict]:
        """Busca, dentro de la lista de un día, el fixture cuyo home/away
        coincida (ver team_match.names_match — emparejamiento ESTRICTO a
        propósito: mejor no encontrar nada que emparejar el partido
        equivocado)."""
        for pred in predictions:
            if names_match(pred.get("home_team"), home_team) and names_match(pred.get("away_team"), away_team):
                return pred
        return None
