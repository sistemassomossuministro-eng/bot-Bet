"""Diagnóstico de pinnapi.com (servicio NO OFICIAL de cuotas de Pinnacle —
ver README/estado del proyecto para la discusión completa de riesgo: no está
afiliado a Pinnacle, existe porque Pinnacle cerró su API pública el 23 de
julio de 2025). UNA sola llamada real, para ver la forma exacta de la
respuesta antes de escribir el parseo definitivo — mismo motivo que ya
aplicamos con PlayerElo y API-Football (ver la nota grande en
playerelo_provider.py): la documentación pública de una API de terceros NO
es suficiente para escribir el parseo con confianza.

IMPORTANTE sobre esta ronda en particular: los ejemplos de la documentación
de pinnapi.com que se revisaron para justificar esta exploración (sport_id
de fútbol, nombres de campos como "money_line"/"periods"/"num_0", forma del
JSON) vinieron de una herramienta de fetch+resumen automático de la página
de docs, NO de una respuesta real de la API. Existe un riesgo real de que
esos nombres estén mal recordados/resumidos — así que este script NO asume
nada de eso como cierto: prueba varias combinaciones razonables (sport_id
candidatos, nombres de header de auth) y imprime la respuesta cruda tal cual
llega, para que el parseo definitivo se escriba sobre datos reales, no sobre
un resumen de marketing.

Uso:
    export PINNAPI_API_KEY="tu-key-gratis-de-pinnapi.com"
    python scripts/verify_pinnapi.py

Cómo conseguir la key gratis (sin tarjeta, según la página pública, ago-2026):
entra a https://pinnapi.com/, busca la opción de generar una key de prueba
("mint one free in a click"), no debería pedir tarjeta de crédito. Si en tu
caso SÍ pide tarjeta o cambió el flujo, avisa antes de seguir — sería una
señal de que la política del servicio cambió desde que se investigó esto.

Copia TODA la salida y pégasela a Claude para escribir el parseo real (o
para confirmar que este servicio no sirve para el proyecto, si la cobertura
de ligas o la forma de la respuesta no calzan).
"""
from __future__ import annotations

import json
import os
import sys

import requests

BASE_URL = "https://pinnapi.com"

# Del docstring de arriba: NO se confía en un único valor de sport_id para
# "soccer" (la fuente es un resumen automático de la página de docs, no una
# respuesta real) — se prueban varios candidatos razonables y se reporta
# cuál responde con datos de fútbol de verdad.
CANDIDATE_SOCCER_SPORT_IDS = [1, 29, 12]

# Ligas de nuestro filtro curado (ver config.example.yaml) que valdría la
# pena reconocer en la respuesta, buscando por substring en minúsculas sobre
# cualquier campo de texto que traiga el nombre de la liga/competencia —
# como no sabemos el nombre exacto que usa pinnapi para cada una, esto es
# solo una búsqueda aproximada para tener una primera señal, no una
# confirmación definitiva.
LEAGUES_TO_LOOK_FOR = [
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "eredivisie", "liga portugal", "super lig", "saudi", "champions league",
    "europa league", "conference league", "libertadores", "sudamericana",
    "colombia", "argentina", "brazil", "brasil", "mexico", "chile", "ecuador",
    "bolivia", "paraguay", "peru", "uruguay", "venezuela", "mls",
]


def _print(data, limit: int = 4000) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    print(text[:limit])
    if len(text) > limit:
        print(f"... [truncado, {len(text)} caracteres en total]")


def _try_get(path: str, api_key: str, params: dict) -> None:
    url = f"{BASE_URL}{path}"
    # Se prueban los 3 métodos de auth que la documentación pública menciona
    # (header recomendado, header alterno, query param) por si alguno no
    # funciona como se espera.
    auth_variants = [
        ("header x-portal-apikey", {"x-portal-apikey": api_key}, {}),
        ("header x-api-key", {"x-api-key": api_key}, {}),
        ("query param key=", {}, {"key": api_key}),
    ]
    for label, headers, extra_params in auth_variants:
        full_params = dict(params, **extra_params)
        print(f"\n--- Intento con {label} — GET {url} params={full_params} ---")
        try:
            resp = requests.get(url, headers=headers, params=full_params, timeout=15)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                _print(data)
                return data
            else:
                print(f"Cuerpo: {resp.text[:1000]}")
        except requests.RequestException as exc:
            print(f"FALLÓ: {exc}")
    return None


def main() -> None:
    api_key = os.environ.get("PINNAPI_API_KEY")
    if not api_key:
        print("Falta la variable de entorno PINNAPI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    found_soccer_data = None
    for sport_id in CANDIDATE_SOCCER_SPORT_IDS:
        print(f"\n{'=' * 70}\nGET /kit/v1/markets sport_id={sport_id} event_type=prematch\n{'=' * 70}")
        data = _try_get("/kit/v1/markets", api_key, {"sport_id": sport_id, "event_type": "prematch"})
        if data and data.get("events"):
            found_soccer_data = data
            print(f"\n>>> sport_id={sport_id} devolvió {len(data['events'])} eventos. Usando este para el resto del diagnóstico.")
            break
        elif data is not None:
            print(f"\n>>> sport_id={sport_id} respondió 200 pero sin eventos (o no es fútbol) — se prueba el siguiente candidato.")

    if not found_soccer_data:
        print("\nNingún sport_id candidato devolvió eventos de fútbol. Revisa la respuesta cruda de arriba a mano.")
        return

    events = found_soccer_data.get("events", [])
    print(f"\n{'=' * 70}\nResumen: {len(events)} eventos de fútbol recibidos (prematch)\n{'=' * 70}")

    # Nombres de liga/competencia únicos vistos en la respuesta — para
    # comparar a mano contra nuestras 26 ligas curadas (ver
    # config.example.yaml), ya que no sabemos si pinnapi usa la misma
    # nomenclatura que odds-api.io.
    league_names = sorted({str(e.get("league_name") or e.get("league") or "?") for e in events})
    print(f"\n{len(league_names)} nombres de liga/competencia distintos encontrados (TODOS, sin recortar):")
    for name in league_names:
        print(f"  - {name}")

    print("\nBúsqueda aproximada de nuestras ligas curadas dentro de esos nombres "
          "(TODOS los matches, no solo el primero — una búsqueda por substring "
          "puede confundir ligas de países distintos, ej. 'super lig' matcheando "
          "'Serbia - Super Liga' en vez de Turquía):")
    lowered = {n.lower(): n for n in league_names}
    for needle in LEAGUES_TO_LOOK_FOR:
        matches = [orig for low, orig in lowered.items() if needle in low]
        status = "✅ SÍ aparece" if matches else "❌ no aparece en este pull"
        print(f"  {needle:25s} -> {status}" + (f" {matches}" if matches else ""))

    # Chequeo específico por PAÍS (no por nombre de liga) para las 4 que
    # quedaron ambiguas en la ronda 1 del diagnóstico (Turquía, Arabia
    # Saudita, USA/MLS, Portugal) — imprime TODAS las competencias de ese
    # país que aparezcan, sea cual sea su nombre exacto.
    print("\nTodas las competencias encontradas para países/casos ambiguos de la ronda 1:")
    for country_prefix in ["turk", "saudi", "usa -", "united states", "portugal"]:
        matches = [orig for low, orig in lowered.items() if low.startswith(country_prefix)]
        print(f"  '{country_prefix}': {matches or '(ninguna)'}")

    # ¿Existe algún mercado tipo BTTS ("ambos anotan")? En el evento de
    # ejemplo solo aparecieron money_line/spreads/totals/team_total(s) — se
    # revisan las claves de TODOS los period['num_0'] (tiempo completo) de
    # una muestra de eventos para confirmar si esto es consistente o si
    # algún evento sí trae un campo extra.
    sample_keys = set()
    for e in events[:200]:
        period_0 = (e.get("periods") or {}).get("num_0") or {}
        sample_keys.update(period_0.keys())
    print(f"\nClaves vistas en periods.num_0 (tiempo completo) sobre {min(200, len(events))} eventos: {sorted(sample_keys)}")
    print("(si no aparece nada parecido a 'btts'/'both_teams_to_score' acá, Pinnacle probablemente "
          "no ofrece ese mercado — habría que seguir usando otra fuente para 'btts' específicamente)")

    print(f"\n{'=' * 70}\nUn evento completo de ejemplo (el primero), forma cruda:\n{'=' * 70}")
    _print(events[0])

    # Si el evento trae un event_id, se prueba también /kit/v1/details para
    # ver si trae algo distinto/adicional al de /markets.
    event_id = events[0].get("event_id") or events[0].get("id")
    if event_id is not None:
        print(f"\n{'=' * 70}\nGET /kit/v1/details event_id={event_id}\n{'=' * 70}")
        _try_get("/kit/v1/details", api_key, {"event_id": event_id})


if __name__ == "__main__":
    main()
