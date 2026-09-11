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
from datetime import datetime, timedelta, timezone
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


def _totals_markets(totals: dict) -> List[BookmakerMarket]:
    """Convierte el dict 'totals' de pinnapi en una lista de BookmakerMarket
    'totals', UNO POR LÍNEA DE PUNTOS — igual que odds_provider.py::
    _parse_event, que también arma un BookmakerMarket separado por línea
    (ver el comentario grande en ese archivo). NUNCA un solo mercado
    combinado con todas las líneas juntas.

    BUG REAL evitado acá (encontrado el 2026-09-07, antes de que llegara a
    afectar picks reales — ver el estado del proyecto para el detalle
    completo): la primera versión de este módulo metía TODAS las líneas de
    puntos (over_0.5/under_0.5, over_1/under_1, ..., over_5.5/under_5.5,
    típicamente 10-15 líneas) en UN SOLO BookmakerMarket. value_finder.py
    asume que cualquier BookmakerMarket que le pase a fair_probabilities()
    es un conjunto mutuamente excluyente y EXHAUSTIVO de resultados de UNA
    sola apuesta (ej. "más/menos de 3 goles" — exactamente 2 resultados que
    suman ~100%+margen) — eso es lo que permite tratar 1/precio como
    probabilidad "justa" tras normalizar. Juntar 10-15 líneas rompe esa
    premisa: la suma de probabilidades implícitas se dispara a ~10-15x el
    vig normal, así que la fair_probability de CUALQUIER línea de totals de
    Pinnacle salía artificialmente minúscula. Esto no fabrica EV positivo
    falso — al contrario, aplasta en silencio EV real cada vez que Pinnacle
    fuera el libro de referencia usado para un pick de totals (ver
    find_value_bets_in_event / 'full_ref_market').

    Para una línea de punto entero (ej. "3", no "3.5"), pinnapi la entrega
    como int en el JSON. No sabemos con certeza si odds-api.io formatearía
    ese mismo punto para Betplay/Bet365 como "over_3" o "over_3.0" (no
    verificado contra una respuesta real). En vez de meter 'over_3' Y
    'over_3.0' en el MISMO mercado (que tendría el mismo problema en
    miniatura: 3 outcomes en vez de 2, sesgando el devig de esa única
    línea), se devuelven DOS mercados clon — uno por cada formato de
    nombre — cada uno con exactamente los 2 outcomes de esa línea. Así,
    sea cual sea el formato real, value_finder.py siempre encuentra un
    mercado de 2 outcomes válido, nunca uno inflado."""
    markets: List[BookmakerMarket] = []
    for line in totals.values():
        if not isinstance(line, dict):
            continue
        points = line.get("points")
        over = line.get("over")
        under = line.get("under")
        if points is None or not isinstance(over, (int, float)) or not isinstance(under, (int, float)):
            continue

        point_strs = {str(points)}
        if isinstance(points, (int, float)) and float(points) == int(points):
            point_strs.add(str(float(points)))  # ej. "2" y "2.0"

        for point_str in point_strs:
            markets.append(
                BookmakerMarket(
                    bookmaker="Pinnacle",
                    market_key="totals",
                    updated_at=None,
                    outcomes=[
                        Outcome(name=f"over_{point_str}", price_decimal=float(over)),
                        Outcome(name=f"under_{point_str}", price_decimal=float(under)),
                    ],
                )
            )
    return markets


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
        markets.extend(_totals_markets(totals))

    return markets


def _parse_starts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _to_naive_utc(dt: datetime) -> datetime:
    """Normaliza un datetime a "naive" en UTC (sin tzinfo) para poder
    restarlo con el resultado de `_parse_starts` (que siempre es naive).

    BUG REAL de producción (2026-09-11, el primer día que el fix del sport
    de 2026-09-07 dejó que este código por fin corriera sobre partidos
    reales): `event.commence_time` viene de
    `odds_provider.py::_parse_datetime`, que en el camino normal SÍ le deja
    el tzinfo (`datetime.fromisoformat("...+00:00")` es "aware"), salvo en
    un fallback raro (`datetime.utcnow()`, naive) si el parseo del proveedor
    falla. Restar un datetime "aware" contra uno "naive" con `-` directo
    lanza `TypeError: can't subtract offset-naive and offset-aware
    datetimes` — y como esto pasaba DENTRO del bucle de emparejamiento
    equipo-por-equipo, tumbaba con una excepción TODA la llamada a
    `attach_pinnacle_reference_markets` para ese día completo (no solo el
    partido problemático), cayendo de vuelta a solo Bet365 en silencio (el
    `except Exception` de `daily.py` lo atrapa y sigue el job, pero
    Pinnacle no emparejó nada esa corrida tampoco, por una razón distinta a
    la del bug anterior). No se había visto nunca porque, antes del fix de
    sport, el bucle externo (`if event.sport != "football": continue`)
    saltaba TODOS los eventos antes de llegar a esta resta — por eso los
    tests con un `Event.commence_time` naive escrito a mano (como
    `_our_event()` en este archivo, antes de este fix) tampoco lo
    detectaron: nunca ejercitaron la forma real (aware) que produce
    `_parse_datetime` en producción."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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
            if starts is not None and abs(starts - _to_naive_utc(event.commence_time)) > MAX_KICKOFF_DIFF:
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
