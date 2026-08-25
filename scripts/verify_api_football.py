"""Diagnóstico de API-Football: UNA sola llamada real por endpoint, para ver
la forma exacta de la respuesta antes de escribir el parseo definitivo (ver
la nota grande en injuries_provider.py sobre por qué este proyecto hace esto
siempre).

Uso:
    export APIFOOTBALL_API_KEY="tu-key-real"
    # Si te suscribiste vía RapidAPI en vez de directo en api-football.com:
    export APIFOOTBALL_VIA_RAPIDAPI=1
    python scripts/verify_api_football.py "Real Madrid"

Copia TODA la salida y pégasela a Claude para que escriba el parseo real con
la forma confirmada (incluyendo cómo emparejar el nombre de equipo de
odds-api.io con el ID interno de API-Football, y qué tan lejos del partido
suele haber datos de lesiones publicados).

HALLAZGO CRÍTICO de la ronda 1 (2026-08-25): `GET /injuries?team=541&season=2026`
respondió 200 OK pero con `errors.plan`: "Free plans do not have access to
this season, try from 2022 to 2024." — el plan gratuito NO cubre la
temporada en curso para /injuries, que es justo lo que este proyecto
necesitaría (bajas antes del partido de HOY). Esta ronda 2 prueba 2 rutas
para ver si hay alguna forma de esquivar esa restricción:
- `/injuries?fixture=<id>` (por partido específico, en vez de team+season) —
  usando un fixture real sacado de `/fixtures?team=<id>&next=1`.
- `/injuries?team=<id>&season=2024` (temporada SÍ permitida) — aunque no
  sirva para hoy, confirma al menos la FORMA real de una respuesta con datos,
  útil para escribir el parseo aunque haya que resolver lo de la temporada
  actual de otra manera (o aceptar que esta señal no es viable en el plan
  gratuito).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.injuries_provider import ApiFootballProvider


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])


def main() -> None:
    api_key = os.environ.get("APIFOOTBALL_API_KEY")
    if not api_key:
        print("Falta la variable de entorno APIFOOTBALL_API_KEY.", file=sys.stderr)
        sys.exit(1)
    via_rapidapi = os.environ.get("APIFOOTBALL_VIA_RAPIDAPI") == "1"
    team_name = sys.argv[1] if len(sys.argv) > 1 else "Real Madrid"

    base_url = "https://api-football-v1.p.rapidapi.com/v3" if via_rapidapi else "https://v3.football.api-sports.io"
    provider = ApiFootballProvider(api_key=api_key, base_url=base_url, via_rapidapi=via_rapidapi)

    print(f"\n{'=' * 70}\nGET /teams search={team_name!r}\n{'=' * 70}")
    try:
        teams_data = provider.raw_get("/teams", {"search": team_name})
        _print(teams_data)
    except Exception as exc:  # noqa: BLE001 — script de diagnóstico
        print(f"FALLÓ: {exc}")
        teams_data = None

    team_id = None
    if teams_data:
        try:
            team_id = teams_data["response"][0]["team"]["id"]
        except (KeyError, IndexError, TypeError):
            print("No se pudo extraer un team id de la respuesta anterior — revísala a mano.")

    if not team_id:
        return

    # (ya sabemos que team+season=2026 falla por el plan gratuito — no se repite)

    # 1) Temporada SÍ permitida por el plan gratuito (2022-2024) — para ver
    #    al menos la FORMA real de una respuesta de /injuries con datos.
    print(f"\n{'=' * 70}\nGET /injuries team={team_id} season=2024 (temporada permitida en el free plan)\n{'=' * 70}")
    try:
        _print(provider.raw_get("/injuries", {"team": team_id, "season": 2024}))
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ: {exc}")

    # 2) Próximos partidos del equipo — a ver si "next" esquiva la restricción
    #    de temporada (si esto también falla, la restricción es más amplia
    #    que solo /injuries).
    next_fixture_id = None
    print(f"\n{'=' * 70}\nGET /fixtures team={team_id} next=1\n{'=' * 70}")
    try:
        fixtures_data = provider.raw_get("/fixtures", {"team": team_id, "next": 1})
        _print(fixtures_data)
        try:
            next_fixture_id = fixtures_data["response"][0]["fixture"]["id"]
        except (KeyError, IndexError, TypeError):
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ: {exc}")

    # 3) Lesiones por FIXTURE específico en vez de team+season — a ver si
    #    esto esquiva la restricción de temporada del punto 1.
    if next_fixture_id is not None:
        print(f"\n{'=' * 70}\nGET /injuries fixture={next_fixture_id}\n{'=' * 70}")
        try:
            _print(provider.raw_get("/injuries", {"fixture": next_fixture_id}))
        except Exception as exc:  # noqa: BLE001
            print(f"FALLÓ: {exc}")
    else:
        print("\nNo se encontró un próximo fixture — no se puede probar /injuries?fixture=<id>.")

    # 4) Lesiones por fecha (hoy) — otra forma alternativa de consultar sin
    #    pasar 'season' explícito, documentada en algunas versiones de la API.
    today = date.today().isoformat()
    print(f"\n{'=' * 70}\nGET /injuries date={today}\n{'=' * 70}")
    try:
        _print(provider.raw_get("/injuries", {"date": today}))
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ: {exc}")


if __name__ == "__main__":
    main()
