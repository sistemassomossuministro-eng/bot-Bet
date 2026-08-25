"""Diagnóstico de PlayerElo: UNA sola llamada real, para ver la forma exacta
de la respuesta antes de escribir el parseo definitivo (ver la nota grande
en playerelo_provider.py sobre por qué este proyecto hace esto siempre).

Uso:
    export PLAYERELO_API_KEY="tu-key-real"
    python scripts/verify_playerelo.py

Prueba un par de rutas plausibles según la documentación pública
(https://playerelo.football/api-access) — como el endpoint EXACTO de
predicciones no está confirmado, este script intenta varias y muestra qué
responde cada una. Copia TODA la salida y pégasela a Claude para que escriba
el parseo real con la forma confirmada.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.playerelo_provider import PlayerEloProvider

CANDIDATE_PATHS = [
    ("/v1/predictions", {"home": "Real Madrid", "away": "Barcelona"}),
    ("/v1/matches/predictions", {"home": "Real Madrid", "away": "Barcelona"}),
    ("/v1/teams", {"search": "Real Madrid"}),
    ("/v1/players", {"search": "Vinicius"}),
]


def main() -> None:
    api_key = os.environ.get("PLAYERELO_API_KEY")
    if not api_key:
        print("Falta la variable de entorno PLAYERELO_API_KEY.", file=sys.stderr)
        sys.exit(1)

    provider = PlayerEloProvider(api_key=api_key)

    for path, params in CANDIDATE_PATHS:
        print(f"\n{'=' * 70}\nGET {path} params={params}\n{'=' * 70}")
        try:
            data = provider.raw_get(path, params)
            print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
        except Exception as exc:  # noqa: BLE001 — script de diagnóstico, queremos ver cualquier fallo
            print(f"FALLÓ: {exc}")


if __name__ == "__main__":
    main()
