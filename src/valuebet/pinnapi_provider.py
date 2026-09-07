"""Cliente para pinnapi.com — cuotas de Pinnacle vía un servicio de TERCEROS,
NO afiliado a Pinnacle (Pinnacle cerró su propia API pública el 23 de julio
de 2025). Ver el estado del proyecto para la discusión completa de riesgo:
al ser una fuente no oficial, puede dejar de funcionar sin aviso — por eso
esta integración es SIEMPRE opcional y con Bet365 (vía odds-api.io) como
respaldo automático, nunca una dependencia dura (ver daily.py::
_enrich_events_with_pinnacle_reference_safely).

FORMA REAL CONFIRMADA (2026-09-07, con `scripts/verify_pinnapi.py` contra la
API real, en 2 rondas — nunca adivinada):

- Auth: header `x-portal-apikey: <api_key>` (confirmado — funcionó al primer
  intento contra la API real).
- `GET /kit/v1/markets?sport_id=1&event_type=prematch` -> TODOS los eventos
  de fútbol del mundo que cubre Pinnacle en una sola llamada (1608 eventos
  en la ronda de verificación) — no hay filtro por liga ni por evento en
  este endpoint. `sport_id=1` = fútbol, confirmado real.
- Cada evento: `{event_id, sport_id, league_id, league_name, starts (ISO8601
  UTC), home, away, event_type, is_have_odds, periods: {"num_0": {...
  PARTIDO COMPLETO...}, "num_1": {...primer tiempo...}, ...}}`. Los mercados
  de tiempo completo están en `periods["num_0"]` — los demás "num_N" son
  medios tiempos/parciales y NO deben usarse acá (mezclarían mercados de
  medio tiempo con los de partido completo).
- `periods["num_0"]` trae `money_line: {home, draw, away}` (mapea directo a
  nuestro mercado 'h2h') y `totals: {"<points>": {points, over, under,
  max}}` (mapea a nuestro 'totals'). NO se vio ningún campo parecido a BTTS
  ("ambos anotan") en 200 eventos revisados — Pinnacle, vía este servicio,
  NO ofrece ese mercado. Por eso este módulo solo produce 'h2h' y 'totals';
  'btts' sigue dependiendo siempre de Bet365.
- `GET /kit/v1/details?event_id=...` devolvió `{"events": []}` (vacío) para
  un evento prematch real — no se usa, `/markets` ya trae todo lo necesario.
- La respuesta incluye ~265 "ligas" distintas, MUCHAS de ellas variantes
  sintéticas del mismo partido para otro mercado (sufijos "Corners"/
  "Bookings") o de categorías que no nos interesan (Women/U19/U21/U23). Por
  eso `PINNAPI_LEAGUE_NAMES` de abajo es una lista blanca EXACTA — nunca se
  filtra por substring/país, para no mezclar por accidente el mercado de
  córners o tarjetas con el de goles de un partido que sí nos interesa.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from .models import BookmakerMarket, Event, Outcome
from .team_match import names_match

logger = logging.getLogger(__name__)

SOCCER_SPORT_ID = 1

# Lista blanca EXACTA de nombres de liga de pinnapi que corresponden a
# nuestras 26 ligas curadas de fútbol (ver config.example.yaml). Verificada
# contra una respuesta real (2026-09-07) — dos quedaron con confianza menor
# porque no aparece ningún nombre obvio de "primera división" masculina para
# ese país en la respuesta real; se eligió la competencia más plausible por
# descarte, marcada abajo. Si un pick de Argentina o Paraguay vía Pinnacle
# sale con un equipo que no reconoces, es la primera señal de que esa
# entrada está mal — revisa con una llamada real a /kit/v1/markets.
PINNAPI_LEAGUE_NAMES = {
    "England - Premier League",
    "Spain - La Liga",
    "Italy - Serie A",
    "Germany - Bundesliga",
    "France - Ligue 1",
    "Netherlands - Eredivisie",
    "Portugal - Primeira Liga",  # nombre viejo — odds-api.io ya usa "Liga Portugal"
    "Turkey - Super League",
    "Saudi Arabia - Pro League",
    "UEFA - Champions League",
    "UEFA - Europa League",
    "UEFA - Conference League",
    "CONMEBOL - Copa Libertadores",
    "CONMEBOL - Copa Sudamericana",
    "Colombia - Primera A",
    "Argentina - Liga Pro",  # ⚠️ confianza media — ver nota de arriba
    "Brazil - Serie A",
    "USA - Major League Soccer",
    "Mexico - Liga MX",
    "Chile - Primera Division",
    "Ecuador - Serie A",
    "Bolivia - Primera Division",
    "Paraguay - Division Profesional",  # ⚠️ confianza media — ver nota de arriba
    "Peru - Liga 1",
    "Uruguay - Primera Division",
    "Venezuela - Primera Division",
}

# Tolerancia para considerar que un evento de pinnapi es "el mismo partido"
# que uno nuestro (mismos equipos, pero cada proveedor puede registrar el
# kickoff con algunos minutos/horas de diferencia). 12h es generoso a
# propósito pero sigue siendo mucho menor que la separación típica entre
# ida y vuelta de un cruce de copa (semanas) — no debería producir falsos
# positivos entre partidos distintos de los mismos dos equipos.
MAX_KICKOFF_DIFF = timedelta(hours=12)


class PinnapiProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://pinnapi.com",
        timeout: int = 20,
        max_retries: int = 3,
    ):
        if not api_key or api_key.startswith("TU_"):
            raise ValueError(
                "Falta configurar 'pinnacle_reference.api_key' en config.yaml. "
                "Consigue una clave gratis (sin tarjeta) en https://pinnapi.com/"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"x-portal-apikey": api_key})

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        # Mismo patrón de reintentos que OddsApiIoProvider._get/PlayerEloProvider.raw_get.
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params or {}, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("retry-after", 5)) or 5
                    logger.warning("pinnapi: rate limit alcanzado, esperando %ss (intento %s)", wait, attempt)
                    time.sleep(min(wait, 60))
                    continue
                if resp.status_code >= 400:
                    logger.warning("pinnapi: respuesta %s de %s: %s", resp.status_code, url, resp.text[:500])
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("pinnapi: error consultando %s (intento %s/%s): %s", url, attempt, self.max_retries, exc)
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"No se pudo consultar {url} tras {self.max_retries} intentos") from last_exc

    def get_soccer_events(self, event_type: str = "prematch") -> List[dict]:
        """UNA sola llamada trae TODOS los partidos de fútbol que cubre
        Pinnacle (mundial, no solo nuestras ligas) — filtrar por
        PINNAPI_LEAGUE_NAMES/equipo es responsabilidad de quien llama (ver
        attach_pinnacle_reference_markets)."""
        data = self._get("/kit/v1/markets", {"sport_id": SOCCER_SPORT_ID, "event_type": event_type})
        return data.get("events", []) if isinstance(data, dict) else []


def _totals_outcomes(totals: dict) -> List[Outcome]:
    """Convierte el dict 'totals' de pinnapi a Outcomes con el mismo formato
    de nombre que usa el resto del proyecto ('over_<punto>'/'under_<punto>',
    ver odds_provider.py). Para un punto que es un número entero (ej. 2, no
    2.5), pinnapi lo entrega como int en el JSON ("2": {"points": 2, ...}) —
    no sabemos con certeza si odds-api.io formatearía ese mismo punto como
    "over_2" o "over_2.0" (no se verificó ese caso puntual contra una
    respuesta real de odds-api.io). Para que el cruce de nombres entre
    proveedores (ver value_finder.py, que compara por IGUALDAD EXACTA de
    string) no falle en silencio solo por este detalle de formato, se
    generan AMBAS variantes apuntando al mismo precio — inofensivo (una
    entrada de más en la lista de outcomes) y evita perder cobertura de
    Pinnacle en líneas de gol enteras por un detalle de formato."""
    outcomes: List[Outcome] = []
    for line in totals.values():
        if not isinstance(line, dict):
            continue
        points = line.get("points")
        if points is None:
            continue
        point_strs = {str(points)}
        if isinstance(points, (int, float)) and float(points) == int(points):
            point_strs.add(str(float(points)))  # ej. "2" y "2.0"

        over = line.get("over")
        under = line.get("under")
        for point_str in point_strs:
            if isinstance(over, (int, float)):
                outcomes.append(Outcome(name=f"over_{point_str}", price_decimal=float(over)))
            if isinstance(under, (int, float)):
                outcomes.append(Outcome(name=f"under_{point_str}", price_decimal=float(under)))
    return outcomes


def _h2h_outcomes(money_line: dict) -> List[Outcome]:
    outcomes = []
    for key in ("home", "draw", "away"):
        price = money_line.get(key)
        if isinstance(price, (int, float)):
            outcomes.append(Outcome(name=key, price_decimal=float(price)))
    return outcomes


def markets_from_pinnapi_event(pinnapi_event: dict) -> List[BookmakerMarket]:
    """Construye los BookmakerMarket 'h2h'/'totals' de Pinnacle para UN
    evento de pinnapi, usando SOLO periods['num_0'] (partido completo — ver
    el aviso grande del módulo). Devuelve [] si el evento no trae ese
    período o no trae ninguno de los 2 mercados."""
    period_0 = (pinnapi_event.get("periods") or {}).get("num_0")
    if not isinstance(period_0, dict):
        return []

    markets: List[BookmakerMarket] = []

    money_line = period_0.get("money_line")
    if isinstance(money_line, dict):
        h2h_outcomes = _h2h_outcomes(money_line)
        if len(h2h_outcomes) >= 2:
            markets.append(BookmakerMarket(bookmaker="Pinnacle", market_key="h2h", updated_at=None, outcomes=h2h_outcomes))

    totals = period_0.get("totals")
    if isinstance(totals, dict):
        totals_outcomes = _totals_outcomes(totals)
        if totals_outcomes:
            markets.append(BookmakerMarket(bookmaker="Pinnacle", market_key="totals", updated_at=None, outcomes=totals_outcomes))

    return markets


def _parse_starts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def attach_pinnacle_reference_markets(events: List[Event], pinnapi_events: List[dict]) -> int:
    """Para cada `Event` (ya con sus cuotas de odds-api.io cargadas), busca
    el partido correspondiente en `pinnapi_events` (misma liga de la lista
    blanca, mismos equipos por nombre — ver team_match.names_match — y
    kickoff dentro de MAX_KICKOFF_DIFF) y, si lo encuentra, le agrega
    `event.bookmakers["Pinnacle"]` con los mercados 'h2h'/'totals'.

    Modifica `events` EN EL LUGAR. Devuelve cuántos eventos se pudieron
    emparejar (puramente informativo para el log de quien llama).

    Deliberadamente ESTRICTO en el emparejamiento (mismo criterio que
    team_match.py en general): un evento nuestro que no encuentra pareja
    exacta simplemente se queda sin mercado de Pinnacle y sigue usando
    Bet365 como referencia — nunca se le asigna el partido "más parecido"."""
    candidates = [pe for pe in pinnapi_events if pe.get("league_name") in PINNAPI_LEAGUE_NAMES]

    matched = 0
    for event in events:
        if event.sport != "football":
            continue
        best = None
        for pe in candidates:
            if not names_match(pe.get("home"), event.home_team):
                continue
            if not names_match(pe.get("away"), event.away_team):
                continue
            starts = _parse_starts(pe.get("starts"))
            if starts is not None and abs(starts - event.commence_time) > MAX_KICKOFF_DIFF:
                continue
            best = pe
            break
        if best is None:
            continue

        markets = markets_from_pinnapi_event(best)
        if not markets:
            continue

        event.bookmakers.setdefault("Pinnacle", [])
        event.bookmakers["Pinnacle"].extend(markets)
        matched += 1

    return matched
