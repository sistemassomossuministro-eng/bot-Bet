"""Emparejamiento de nombres de equipo entre proveedores distintos.

odds-api.io, PlayerElo y API-Football no comparten ningún ID de equipo entre
sí, así que la única forma de correlacionar un partido/equipo entre ellos es
por NOMBRE — y cada proveedor escribe los nombres un poco distinto ("Real
Madrid" vs "Real Madrid CF", con o sin acentos, etc.).

Filosofía DELIBERADAMENTE ESTRICTA (decisión explícita del usuario del
proyecto, 2026-08-25): mejor NO mostrar una señal secundaria que mostrar la
de un equipo o partido equivocado. `names_match` solo acepta coincidencia
exacta después de normalizar (acentos, mayúsculas, puntuación) y de quitar
un pequeño set de sufijos genéricos sin identidad propia ("FC", "CF", etc.)
— nunca coincidencia parcial/difusa. Si ves en el log que un equipo real no
está emparejando por una variación de nombre real (ej. traducciones como
"Bayern München" vs "Bayern Munich"), es candidato a sumarse a NAME_ALIASES
de abajo — pero solo con un caso real confirmado, nunca adivinado.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Sufijos/prefijos que son solo la forma legal del club, sin identidad propia
# — quitarlos ayuda a emparejar "Real Madrid" (PlayerElo) con "Real Madrid CF"
# (si algún proveedor lo escribiera así) sin arriesgar falsos positivos entre
# clubes distintos (por eso NO se quitan palabras con identidad como "Real",
# "United", "City", "Atlético").
_GENERIC_TOKENS = {"fc", "cf", "sc", "ac", "cd", "afc", "ssc", "ud", "if", "bk", "sk", "fk", "ff", "ec"}

# Traducciones/variantes de nombre confirmadas a mano contra un caso real —
# ver la nota de arriba. Vacío hasta que se observe un caso real en el log.
NAME_ALIASES: dict[str, str] = {}


def normalize_team_name(name: str) -> str:
    """minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return NAME_ALIASES.get(s, s)


def _core_tokens(name: str) -> list[str]:
    normalized = normalize_team_name(name)
    return [t for t in normalized.split() if t not in _GENERIC_TOKENS]


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    """True solo si los nombres coinciden exactamente tras normalizar (y quitar
    sufijos genéricos sin identidad). Deliberadamente estricto — ver el aviso
    grande al inicio del archivo."""
    if not a or not b:
        return False
    if normalize_team_name(a) == normalize_team_name(b):
        return True
    ta, tb = _core_tokens(a), _core_tokens(b)
    return bool(ta) and ta == tb
