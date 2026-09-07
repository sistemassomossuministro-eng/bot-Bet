"""Lógica (sin red, 100% testeable) del chequeo semanal de slugs de ligas de
fútbol — ver scripts/check_league_slugs.py para el CLI que la usa y el
workflow weekly_league_slug_check.yml para cómo se programa.

Contexto (ver README, "Riesgos de mantenimiento del filtro de fútbol" y el
bug real de sep-2026 con los slugs de UEFA): odds-api.io a veces le cambia el
nombre al slug de una liga cuando cambia de fase (ej. "Apertura" ->
"Clausura", o le agregan/quitan un sufijo como "-playoff-round"). Cuando eso
pasa, el slug viejo simplemente deja de existir y esa liga deja de traer
partidos en silencio (sin error, 0 eventos). Este módulo detecta esos casos
comparando los slugs configurados contra una respuesta real de
GET /leagues?sport=football, y decide con qué confianza se puede reemplazar
un slug automáticamente:

- Confianza alta (auto-reemplazable): el slug configurado ya no existe, pero
  hay EXACTAMENTE una liga viva que es "la misma" competencia sin su sufijo
  de fase (o con otro sufijo de fase distinto). Esto cubre los dos casos
  reales ya vistos: UEFA (le quitaron el sufijo) y el riesgo ya documentado
  de Argentina/México/Paraguay/Perú/Uruguay/Venezuela (cambia de sufijo de
  fase, ej. Apertura <-> Clausura).
- Ambiguo (requiere revisión humana): 0 candidatos (la liga puede haber
  desaparecido del todo, o cambiar de nombre de forma que este heurístico no
  reconoce) o más de 1 candidato (no hay forma segura de saber cuál es la
  competencia correcta sin adivinar — y este proyecto tiene la regla de NUNCA
  adivinar nombres/slugs de una API externa).

A propósito NO se usa un parser YAML completo (PyYAML) para editar
config.example.yaml: PyYAML no conserva comentarios al volver a serializar, y
este archivo depende por completo de sus comentarios inline para documentar
advertencias de mantenimiento — perderlos sería mucho peor que el problema
que este chequeo intenta resolver. En cambio, se opera sobre el texto crudo
con expresiones regulares acotadas a las líneas con el formato
`      - "slug-en-minusculas"` (el único formato usado en el bloque
`leagues_by_sport.football`; otras listas del archivo, como la de
basketball, usan una sola línea tipo `["usa-nba"]` y esta regex las ignora
a propósito).
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# Sufijos de "fase de torneo" observados hasta ahora en slugs reales de
# odds-api.io. Si en el futuro aparece un slug con un sufijo de fase nuevo
# que no está en esta lista, este chequeo simplemente no lo reconocerá como
# "el mismo torneo" y lo va a reportar como ambiguo/sin candidatos en vez de
# auto-actualizarlo — es el comportamiento seguro por defecto (ver el
# docstring del módulo). Amplía esta lista si eso llega a pasar.
KNOWN_PHASE_SUFFIXES: Tuple[str, ...] = (
    "-playoff-round",
    "-knockout-stage",
    "-clausura",
    "-apertura",
    "-finalizacion",
    "-regular-season",
    "-relegation-round",
    "-promotion-round",
    "-championship-round",
    "-group-stage",
)

_SLUG_LINE_RE = re.compile(r'^(?P<indent>[ \t]*)-\s*"(?P<slug>[a-z0-9][a-z0-9-]*)"', re.MULTILINE)
_FOOTBALL_HEADER_RE = re.compile(r"^ {4}football:.*$", re.MULTILINE)
# Línea con 0-2 espacios de indentación seguida de contenido -> significa que
# volvimos al nivel de 'leagues_by_sport' (o más arriba todavía), es decir,
# que el bloque de football ya terminó.
_DEDENT_RE = re.compile(r"^ {0,2}\S", re.MULTILINE)


def strip_phase_suffix(slug: str) -> str:
    """Quita un sufijo de fase conocido, si lo tiene. Si no tiene ninguno de
    los conocidos, devuelve el slug tal cual (esto es lo que permite
    detectar tanto "le quitaron el sufijo" como "le cambiaron el sufijo")."""
    for suffix in KNOWN_PHASE_SUFFIXES:
        if slug.endswith(suffix):
            return slug[: -len(suffix)]
    return slug


def _extract_football_block(yaml_text: str) -> str:
    header_match = _FOOTBALL_HEADER_RE.search(yaml_text)
    if not header_match:
        raise ValueError(
            "No se encontró 'football:' dentro de leagues_by_sport (con 4 espacios de "
            "indentación) — ¿cambió el formato de config.example.yaml? Revisa el archivo "
            "a mano antes de confiar en este chequeo."
        )
    start = header_match.end()
    dedent_match = _DEDENT_RE.search(yaml_text, pos=start)
    end = dedent_match.start() if dedent_match else len(yaml_text)
    return yaml_text[start:end]


def extract_football_league_slugs(yaml_text: str) -> List[str]:
    """Extrae, en orden, los slugs listados en `leagues_by_sport.football`."""
    football_block = _extract_football_block(yaml_text)
    return [m.group("slug") for m in _SLUG_LINE_RE.finditer(football_block)]


def find_slug_changes(
    configured_slugs: List[str], live_slugs: List[str]
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """Compara los slugs configurados contra los que la API devuelve HOY.

    Devuelve (confident, ambiguous):
    - confident: {slug_viejo: slug_nuevo} — casos con un único reemplazo
      seguro, listos para aplicar con apply_renames_to_config_text().
    - ambiguous: {slug_viejo: [candidatos]} — el slug ya no existe y no hay
      un reemplazo seguro. La lista de candidatos puede estar vacía (no se
      encontró ninguna liga parecida) o tener 2+ (no se puede saber cuál es
      la correcta sin adivinar). Ninguno de estos se toca automáticamente.

    Un slug configurado que SIGUE existiendo tal cual en `live_slugs` no
    aparece en ninguno de los dos resultados — no hay nada que reportar."""
    live_set = set(live_slugs)
    live_by_stem: Dict[str, List[str]] = {}
    for live_slug in live_slugs:
        live_by_stem.setdefault(strip_phase_suffix(live_slug), []).append(live_slug)

    confident: Dict[str, str] = {}
    ambiguous: Dict[str, List[str]] = {}
    for slug in configured_slugs:
        if slug in live_set:
            continue
        stem = strip_phase_suffix(slug)
        candidates = [c for c in live_by_stem.get(stem, []) if c != slug]
        if len(candidates) == 1:
            confident[slug] = candidates[0]
        else:
            ambiguous[slug] = candidates
    return confident, ambiguous


def apply_renames_to_config_text(yaml_text: str, renames: Dict[str, str], today: str) -> str:
    """Aplica los renames YA DECIDIDOS (el lado `confident` de find_slug_changes)
    al texto crudo del archivo: reemplaza el string del slug afectado en su
    línea y agrega, justo arriba, una línea de comentario con la fecha
    explicando el cambio. No toca ninguna otra parte del archivo (ni
    reformatea, ni reordena, ni reescribe otros comentarios) — así el resto
    de la documentación inline del archivo se conserva intacta."""
    if not renames:
        return yaml_text

    out_lines: List[str] = []
    for line in yaml_text.split("\n"):
        match = _SLUG_LINE_RE.match(line)
        old_slug = match.group("slug") if match else None
        if match and old_slug in renames:
            new_slug = renames[old_slug]
            indent = match.group("indent")
            out_lines.append(
                f'{indent}# 🤖 auto-actualizado {today} por scripts/check_league_slugs.py: la API '
                f'renombró este slug (antes "{old_slug}", ya no existía en '
                f'GET /leagues?sport=football). Revisa que el comentario original de abajo '
                f"siga describiendo bien esta liga/fase."
            )
            out_lines.append(line.replace(f'"{old_slug}"', f'"{new_slug}"', 1))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
