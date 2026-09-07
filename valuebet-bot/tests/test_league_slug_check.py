"""Tests del chequeo semanal de slugs de ligas de fútbol (ver
src/valuebet/league_slug_check.py). Reproducen los dos casos reales ya vistos
en producción (UEFA le quitaron el sufijo de fase; Argentina/México cambian
de sufijo Apertura<->Clausura) más los casos ambiguos que a propósito NUNCA
deben auto-actualizarse (0 o 2+ candidatos)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.league_slug_check import (
    apply_renames_to_config_text,
    extract_football_league_slugs,
    find_slug_changes,
    strip_phase_suffix,
)

SAMPLE_CONFIG = """\
odds_provider:
  leagues_by_sport:
    basketball: ["usa-nba"]                 # esta línea NO debe contarse como slug de fútbol
    football:                               # comentario del bloque
      - "colombia-liga-dimayor-finalizacion"   # Colombia
      - "argentina-primera-lpf-apertura"        # ⚠️ riesgo Apertura/Clausura
      - "international-clubs-uefa-champions-league-playoff-round"  # UEFA
      - "brazil-brasileiro-serie-a"              # temporada única
  poll_interval_seconds: 120
  lookahead_days: 3
"""


def test_strip_phase_suffix_known_suffixes():
    assert strip_phase_suffix("mexico-liga-mx-apertura") == "mexico-liga-mx"
    assert strip_phase_suffix("mexico-liga-mx-clausura") == "mexico-liga-mx"
    assert (
        strip_phase_suffix("international-clubs-uefa-champions-league-playoff-round")
        == "international-clubs-uefa-champions-league"
    )


def test_strip_phase_suffix_no_known_suffix_returns_unchanged():
    assert strip_phase_suffix("brazil-brasileiro-serie-a") == "brazil-brasileiro-serie-a"


def test_extract_football_league_slugs_ignores_basketball_inline_list():
    slugs = extract_football_league_slugs(SAMPLE_CONFIG)
    assert slugs == [
        "colombia-liga-dimayor-finalizacion",
        "argentina-primera-lpf-apertura",
        "international-clubs-uefa-champions-league-playoff-round",
        "brazil-brasileiro-serie-a",
    ]
    assert "usa-nba" not in slugs


def test_find_slug_changes_confident_when_suffix_was_removed():
    # Caso real: UEFA. El slug configurado tenía "-playoff-round", la API ya
    # no lo tiene pero SÍ tiene la versión sin sufijo -> reemplazo seguro.
    configured = ["international-clubs-uefa-champions-league-playoff-round"]
    live = ["international-clubs-uefa-champions-league"]

    confident, ambiguous = find_slug_changes(configured, live)

    assert confident == {
        "international-clubs-uefa-champions-league-playoff-round": "international-clubs-uefa-champions-league"
    }
    assert ambiguous == {}


def test_find_slug_changes_confident_when_phase_suffix_changed():
    # Caso real esperado: Argentina/México pasan de Apertura a Clausura (o
    # viceversa) y el slug viejo desaparece.
    configured = ["mexico-liga-mx-apertura"]
    live = ["mexico-liga-mx-clausura", "spain-laliga"]

    confident, ambiguous = find_slug_changes(configured, live)

    assert confident == {"mexico-liga-mx-apertura": "mexico-liga-mx-clausura"}
    assert ambiguous == {}


def test_find_slug_changes_still_existing_slug_is_not_reported():
    configured = ["spain-laliga"]
    live = ["spain-laliga"]

    confident, ambiguous = find_slug_changes(configured, live)

    assert confident == {}
    assert ambiguous == {}


def test_find_slug_changes_ambiguous_with_no_candidates():
    configured = ["angola-girabola"]
    live = ["spain-laliga"]  # nada parecido

    confident, ambiguous = find_slug_changes(configured, live)

    assert confident == {}
    assert ambiguous == {"angola-girabola": []}


def test_find_slug_changes_ambiguous_with_multiple_candidates():
    # Ambos "candidatos" comparten el mismo stem — no hay forma segura de
    # saber cuál es el reemplazo correcto, así que NO se auto-actualiza.
    configured = ["paraguay-division-de-honor-apertura"]
    live = [
        "paraguay-division-de-honor-clausura",
        "paraguay-division-de-honor-finalizacion",
    ]

    confident, ambiguous = find_slug_changes(configured, live)

    assert confident == {}
    assert sorted(ambiguous["paraguay-division-de-honor-apertura"]) == [
        "paraguay-division-de-honor-clausura",
        "paraguay-division-de-honor-finalizacion",
    ]


def test_apply_renames_to_config_text_replaces_slug_and_adds_dated_comment():
    renames = {"argentina-primera-lpf-apertura": "argentina-primera-lpf-clausura"}

    new_text = apply_renames_to_config_text(SAMPLE_CONFIG, renames, "2026-09-14")

    # El slug viejo ya no debe quedar como VALOR de una entrada de la lista
    # (solo puede seguir mencionado dentro del comentario explicativo nuevo).
    assert '- "argentina-primera-lpf-apertura"' not in new_text
    assert '- "argentina-primera-lpf-clausura"' in new_text
    assert '"argentina-primera-lpf-clausura"' not in SAMPLE_CONFIG  # no estaba antes
    assert (
        "# 🤖 auto-actualizado 2026-09-14 por scripts/check_league_slugs.py" in new_text
    )
    # No debe tocar ninguna otra línea del archivo.
    assert '"colombia-liga-dimayor-finalizacion"' in new_text
    assert '"brazil-brasileiro-serie-a"' in new_text
    assert 'basketball: ["usa-nba"]' in new_text


def test_apply_renames_to_config_text_no_renames_returns_text_unchanged():
    assert apply_renames_to_config_text(SAMPLE_CONFIG, {}, "2026-09-14") == SAMPLE_CONFIG


def test_apply_renames_to_config_text_only_touches_matching_slug():
    renames = {"argentina-primera-lpf-apertura": "argentina-primera-lpf-clausura"}

    new_text = apply_renames_to_config_text(SAMPLE_CONFIG, renames, "2026-09-14")

    # El slug de UEFA (no incluido en 'renames') debe quedar intacto.
    assert '"international-clubs-uefa-champions-league-playoff-round"' in new_text
    assert '"argentina-primera-lpf-clausura"' in new_text
