"""Diagnóstico de PlayerElo: UNA sola llamada real, para ver la forma exacta
de la respuesta antes de escribir el parseo definitivo (ver la nota grande
en playerelo_provider.py sobre por qué este proyecto hace esto siempre).

Uso:
    export PLAYERELO_API_KEY="tu-key-real"
    python scripts/verify_playerelo.py

Ronda 1 (2026-08-24, primera corrida) ya confirmó:
- GET /v1/predictions        -> 200 OK. Devuelve una lista de fixtures
  próximos/en curso, CADA UNO con su propia predicción (home_team_elo,
  away_team_elo, p_home, p_draw, p_away — a veces null si PlayerElo no
  tiene rating para esos jugadores). Los parámetros 'home'/'away' que se
  probaron NO filtraron nada — la respuesta no cambió según el equipo
  pedido, así que esta ronda agrega 'limit'/'offset'/'date' como candidatos.
- GET /v1/matches/predictions -> 404, no existe.
- GET /v1/teams               -> 404, no existe.
- GET /v1/players?search=...  -> 200 OK, 'search' SÍ filtra por nombre.

La página pública https://playerelo.football/api-access también menciona
(sin parámetros documentados explícitamente):
- GET /v1/fixtures/{id}/prediction  ("Fixture prediction + scoreline odds")
- GET /v1/clubs                     ("Clubs ranked by Team Elo")

Esta ronda 2 prueba esos dos, más variantes de filtro sobre /v1/predictions,
encadenando: primero pide /v1/predictions, saca un fixture_id real de la
respuesta, y lo usa para probar /v1/fixtures/{id}/prediction — así no hace
falta adivinar un ID a mano.

Copia TODA la salida y pégasela a Claude para que escriba el parseo real con
la forma confirmada.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.playerelo_provider import PlayerEloProvider


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])


def main() -> None:
    api_key = os.environ.get("PLAYERELO_API_KEY")
    if not api_key:
        print("Falta la variable de entorno PLAYERELO_API_KEY.", file=sys.stderr)
        sys.exit(1)

    provider = PlayerEloProvider(api_key=api_key)

    # 1) /v1/predictions liso (ya confirmado 200 OK) — pero ahora probamos si
    #    'limit'/'offset'/'date' cambian algo, ya que 'home'/'away' no sirvieron.
    print(f"\n{'=' * 70}\nGET /v1/predictions (sin filtros)\n{'=' * 70}")
    base_predictions = None
    try:
        base_predictions = provider.raw_get("/v1/predictions")
        _print(base_predictions)
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ: {exc}")

    for params in (
        {"limit": 5},
        {"date": "2026-08-25"},
    ):
        print(f"\n{'=' * 70}\nGET /v1/predictions params={params}\n{'=' * 70}")
        try:
            _print(provider.raw_get("/v1/predictions", params))
        except Exception as exc:  # noqa: BLE001
            print(f"FALLÓ: {exc}")

    # 2) Con un fixture_id real (sacado de la respuesta base), probar el
    #    endpoint de detalle que menciona la página pública de PlayerElo.
    #    Preferimos uno CON elo/probabilidades ya calculadas (no null) para
    #    ver el campo 'scoreline_distribution' realmente poblado, no vacío.
    fixture_id = None
    if isinstance(base_predictions, list):
        with_elo = [
            item
            for item in base_predictions
            if isinstance(item, dict) and item.get("fixture_id") is not None and item.get("p_home") is not None
        ]
        pool = with_elo or base_predictions
        for item in pool:
            if isinstance(item, dict) and item.get("fixture_id") is not None:
                fixture_id = item["fixture_id"]
                break
    if fixture_id is not None:
        print(f"\n{'=' * 70}\nGET /v1/fixtures/{fixture_id}/prediction (elegido con p_home no-nulo si había alguno)\n{'=' * 70}")
        try:
            _print(provider.raw_get(f"/v1/fixtures/{fixture_id}/prediction"))
        except Exception as exc:  # noqa: BLE001
            print(f"FALLÓ: {exc}")
    else:
        print("\nNo se encontró ningún fixture_id en /v1/predictions — no se puede probar /v1/fixtures/{id}/prediction.")

    # 3) /v1/clubs (mencionado en la página pública, forma de búsqueda sin confirmar).
    for path, params in (
        ("/v1/clubs", None),
        ("/v1/clubs", {"search": "Real Madrid"}),
    ):
        print(f"\n{'=' * 70}\nGET {path} params={params}\n{'=' * 70}")
        try:
            _print(provider.raw_get(path, params))
        except Exception as exc:  # noqa: BLE001
            print(f"FALLÓ: {exc}")


if __name__ == "__main__":
    main()
