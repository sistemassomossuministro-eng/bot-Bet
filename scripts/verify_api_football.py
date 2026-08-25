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
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.injuries_provider import ApiFootballProvider


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
        print(json.dumps(teams_data, indent=2, ensure_ascii=False)[:3000])
    except Exception as exc:  # noqa: BLE001 — script de diagnóstico
        print(f"FALLÓ: {exc}")
        teams_data = None

    team_id = None
    if teams_data:
        try:
            team_id = teams_data["response"][0]["team"]["id"]
        except (KeyError, IndexError, TypeError):
            print("No se pudo extraer un team id de la respuesta anterior — revísala a mano.")

    if team_id:
        print(f"\n{'=' * 70}\nGET /injuries team={team_id}\n{'=' * 70}")
        try:
            injuries_data = provider.raw_get("/injuries", {"team": team_id, "season": 2026})
            print(json.dumps(injuries_data, indent=2, ensure_ascii=False)[:3000])
        except Exception as exc:  # noqa: BLE001
            print(f"FALLÓ: {exc}")


if __name__ == "__main__":
    main()
