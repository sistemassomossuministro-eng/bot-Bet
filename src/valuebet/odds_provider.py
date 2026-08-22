"""Clientes de proveedores de datos de cuotas (SOLO LECTURA).

IMPORTANTE:
Estos clientes únicamente leen cuotas públicamente mostradas por proveedores
de datos de terceros. No inician sesión en ninguna casa de apuestas ni
colocan apuestas. La colocación de apuestas la hace siempre una persona,
manualmente, en la app/web de la casa de apuestas.

El adaptador por defecto (OddsApiIoProvider) está construido contra la
documentación pública de https://docs.odds-api.io (consultada en ago-2026).
Las APIs de terceros cambian con el tiempo: si algo falla, compara la
respuesta real (imprime `response.json()`) contra lo que este archivo asume
y ajusta el parseo — especialmente el mapeo de nombres de mercado en
`_MARKET_NAME_MAP`, ya que la documentación pública solo mostraba un
ejemplo completo (mercado "ML" / 1x2).
"""
from __future__ import annotations

import abc
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

import requests

from dataclasses import dataclass

from .models import BookmakerMarket, Event, Outcome


@dataclass
class EventResult:
    """Resultado (posiblemente parcial o inexistente aún) de un evento ya jugado."""
    event_id: str
    status: str                     # ej. "pending" | "live" | "settled" (según el proveedor)
    home_score: Optional[int]
    away_score: Optional[int]

    @property
    def is_settled(self) -> bool:
        return self.status == "settled" and self.home_score is not None and self.away_score is not None

logger = logging.getLogger(__name__)

# Mapeo best-effort de nombres de mercado del proveedor -> claves internas normalizadas.
# Amplía este diccionario según los mercados reales que veas en las respuestas.
_MARKET_NAME_MAP = {
    "ML": "h2h",
    "1X2": "h2h",
    "Moneyline": "h2h",
    "Over/Under": "totals",
    "Total Goals": "totals",
    "Handicap": "spreads",
    "Asian Handicap": "spreads",
}


def _normalize_market_key(raw_name: str) -> str:
    return _MARKET_NAME_MAP.get(raw_name, raw_name.strip().lower().replace(" ", "_").replace("/", "_"))


def _parse_odds_line(line: dict, market_key: Optional[str] = None) -> List[Outcome]:
    """Convierte un dict de una línea de cuotas (ej. {'home': '2.10', 'draw': '3.40', 'away': '3.20'})
    en una lista de Outcome. Si la línea trae un 'point' (totales/hándicap), se anexa al nombre
    del resultado para distinguir líneas distintas del mismo mercado (ej. 'over_2.5').

    Para 'totals'/'spreads' un 'point' es obligatorio: sin él, dos líneas de puntos
    distintos (ej. "más de 0.5 goles" y "más de 4.5 goles") colapsarían al mismo
    nombre de resultado ("over") y terminarían comparándose entre sí como si fueran
    la misma apuesta — así se descubrió, en producción, un bug que producía EV de
    +700% (un pick "over 8.5" evaluado con la probabilidad justa de un "over 2.5"
    completamente distinto). Se descarta la línea entera en ese caso, en vez de
    arriesgar ese cruce silencioso."""
    point = line.get("point")
    if point is None and market_key in ("totals", "spreads"):
        logger.warning(
            "Línea de '%s' sin 'point' — se descarta para no cruzar resultados de líneas distintas: %s",
            market_key,
            line,
        )
        return []

    outcomes = []
    for key, value in line.items():
        if key == "point":
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        name = f"{key}_{point}" if point is not None else key
        outcomes.append(Outcome(name=name, price_decimal=price))
    return outcomes


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("No se pudo parsear fecha: %s", value)
        return None


class OddsProvider(abc.ABC):
    """Interfaz que debe cumplir cualquier proveedor de datos de cuotas."""

    @abc.abstractmethod
    def list_events(
        self,
        sport: str,
        leagues: Optional[List[str]] = None,
        lookahead_days: int = 3,
        limit: Optional[int] = None,
    ) -> List[Event]:
        """Devuelve eventos próximos (sin cuotas necesariamente pobladas).

        leagues=None (o lista vacía) significa TODAS las ligas del deporte —
        es el valor por defecto del proyecto, para cubrir fútbol mundial y no
        solo torneos colombianos."""

    @abc.abstractmethod
    def get_event_odds(self, event_id: str, bookmakers: List[str]) -> Event:
        """Devuelve un evento con las cuotas de los bookmakers solicitados."""

    @abc.abstractmethod
    def get_events_odds(self, event_ids: List[str], bookmakers: List[str]) -> List[Event]:
        """Igual que get_event_odds pero para muchos eventos a la vez (mismo número
        de resultados que event_ids, en lo posible) — mucho más eficiente en
        cuota de API cuando hay que cubrir cientos de partidos por día (fútbol
        mundial, no solo una liga)."""

    @abc.abstractmethod
    def get_event_result(self, event_id: str) -> "EventResult":
        """Devuelve el estado/marcador actual de un evento (para liquidar picks del día anterior)."""


class OddsApiIoProvider(OddsProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.odds-api.io/v3",
        timeout: int = 15,
        max_retries: int = 3,
    ):
        if not api_key or api_key.startswith("TU_"):
            raise ValueError(
                "Falta configurar 'odds_provider.api_key' en config.yaml. "
                "Consigue una clave en https://odds-api.io"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self.api_key
        url = f"{self.base_url}{path}"

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("x-ratelimit-reset", 5)) or 5
                    logger.warning("Rate limit alcanzado, esperando %ss (intento %s)", wait, attempt)
                    time.sleep(min(wait, 60))
                    continue
                if resp.status_code >= 400:
                    # Sin esto, un 400 solo deja "Bad Request" en el log — sin decir
                    # POR QUÉ (ej. un bookmaker no soportado por el proveedor). El
                    # cuerpo de la respuesta casi siempre trae el motivo exacto.
                    logger.warning(
                        "Respuesta %s de %s: %s", resp.status_code, url, resp.text[:500]
                    )
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Error consultando %s (intento %s/%s): %s", url, attempt, self.max_retries, exc)
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                    # Un 4xx (salvo 429, ya manejado arriba) es un problema de la
                    # request en sí — reintentar no lo va a arreglar, y solo demora
                    # el fallo. Se corta rápido para que el resto del job no espere.
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"No se pudo consultar {url} tras {self.max_retries} intentos") from last_exc

    def list_events(
        self,
        sport: str,
        leagues: Optional[List[str]] = None,
        lookahead_days: int = 3,
        limit: Optional[int] = None,
    ) -> List[Event]:
        now = datetime.utcnow()
        params = {
            "sport": sport,
            "status": "pending",
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + timedelta(days=lookahead_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": limit or 5000,
        }
        # Sin `leagues` (o lista vacía) NO se manda el filtro 'league' -> la API
        # devuelve eventos de TODAS las ligas de fútbol del mundo para las que
        # tenga datos. Este es el modo por defecto del proyecto.
        if leagues:
            params["league"] = ",".join(leagues)

        data = self._get("/events", params)
        raw_events = data if isinstance(data, list) else data.get("data", data.get("events", []))

        events = []
        for raw in raw_events:
            events.append(self._parse_event(raw))
        return events

    def get_event_odds(self, event_id: str, bookmakers: List[str]) -> Event:
        params = {"eventId": event_id, "bookmakers": ",".join(bookmakers)}
        data = self._get("/odds", params)
        return self._parse_event(data)

    def get_events_odds(self, event_ids: List[str], bookmakers: List[str]) -> List[Event]:
        """Usa GET /odds/multi (hasta 10 event ids por llamada) para no gastar
        una consulta de API por cada partido — imprescindible cuando se cubre
        fútbol mundial y puede haber cientos de partidos en la ventana del día.
        """
        events: List[Event] = []
        bookmakers_param = ",".join(bookmakers)
        for i in range(0, len(event_ids), 10):
            chunk = event_ids[i : i + 10]
            params = {"eventIds": ",".join(chunk), "bookmakers": bookmakers_param}
            data = self._get("/odds/multi", params)
            raw_events = data if isinstance(data, list) else data.get("data", data.get("events", []))
            for raw in raw_events:
                events.append(self._parse_event(raw))
        return events

    def get_event_result(self, event_id: str) -> EventResult:
        """Consulta el estado/marcador de un evento vía GET /events/{id}.

        Basado en la documentación pública de odds-api.io (ago-2026): los eventos
        ya jugados exponen status='settled' y un objeto scores={'home': N, 'away': N}.
        Si el evento aún no se ha jugado o el proveedor todavía no publicó el
        resultado, se devuelve status tal cual venga (ej. 'pending' o 'live') y
        scores en None — quien llama debe reintentar en una ejecución posterior.
        """
        data = self._get(f"/events/{event_id}")
        status = data.get("status", "pending")
        scores = data.get("scores") or {}
        home_score = scores.get("home")
        away_score = scores.get("away")
        return EventResult(
            event_id=str(data.get("id", event_id)),
            status=status,
            home_score=int(home_score) if home_score is not None else None,
            away_score=int(away_score) if away_score is not None else None,
        )

    @staticmethod
    def _parse_event(raw: dict) -> Event:
        bookmakers_raw: Dict[str, list] = raw.get("bookmakers", {}) or {}
        bookmakers: Dict[str, List[BookmakerMarket]] = {}
        for bk_name, markets in bookmakers_raw.items():
            parsed_markets = []
            for m in markets:
                market_key = _normalize_market_key(m.get("name", ""))
                updated_at = _parse_datetime(m.get("updatedAt"))
                for line in m.get("odds", []):
                    outcomes = _parse_odds_line(line, market_key)
                    if outcomes:
                        parsed_markets.append(
                            BookmakerMarket(
                                bookmaker=bk_name,
                                market_key=market_key,
                                updated_at=updated_at,
                                outcomes=outcomes,
                            )
                        )
            bookmakers[bk_name] = parsed_markets

        sport = raw.get("sport", {}).get("name", "") if isinstance(raw.get("sport"), dict) else str(raw.get("sport", ""))
        league = raw.get("league", {}).get("name", "") if isinstance(raw.get("league"), dict) else str(raw.get("league", ""))

        return Event(
            event_id=str(raw.get("id")),
            sport=sport,
            league=league,
            home_team=raw.get("home", "?"),
            away_team=raw.get("away", "?"),
            commence_time=_parse_datetime(raw.get("date")) or datetime.utcnow(),
            status=raw.get("status", "pending"),
            bookmakers=bookmakers,
        )


def build_provider(config: dict) -> OddsProvider:
    """Factory sencilla para instanciar el proveedor configurado en config.yaml."""
    name = config.get("name", "odds_api_io")
    if name == "odds_api_io":
        return OddsApiIoProvider(
            api_key=config["api_key"],
            base_url=config.get("base_url", "https://api.odds-api.io/v3"),
        )
    raise ValueError(f"Proveedor de cuotas desconocido: {name}")
