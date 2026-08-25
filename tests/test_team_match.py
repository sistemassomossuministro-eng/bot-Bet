import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.team_match import names_match, normalize_team_name


def test_exact_match_after_normalizing_accents_and_case():
    assert names_match("Bayern München", "bayern münchen")
    assert names_match("Atlético Nacional", "ATLETICO NACIONAL")


def test_generic_suffix_does_not_block_match():
    """'Real Madrid' (PlayerElo) debe emparejar con 'Real Madrid CF' si algún
    proveedor lo escribiera con el sufijo legal — pero solo quitando
    sufijos SIN identidad propia (fc/cf/...), nunca palabras con identidad."""
    assert names_match("Real Madrid", "Real Madrid CF")
    assert names_match("River Plate", "River Plate AC")


def test_different_clubs_never_match():
    """Caso real de riesgo: NO debe emparejar equipos distintos que
    comparten una palabra (ej. dos 'Real ...')."""
    assert not names_match("Real Madrid", "Real Sociedad")
    assert not names_match("Real Madrid", "Real Betis")


def test_translation_variant_does_not_match_without_explicit_alias():
    """Deliberadamente estricto: sin un alias explícito confirmado, una
    traducción real distinta (no solo acentos) NO se considera match —
    mejor omitir la señal que arriesgar un emparejamiento incorrecto."""
    assert not names_match("Bayern München", "Bayern Munich")


def test_empty_or_none_never_matches():
    assert not names_match("", "Real Madrid")
    assert not names_match("Real Madrid", None)
    assert not names_match(None, None)


def test_normalize_team_name_basic():
    assert normalize_team_name("Atlético Nacional") == "atletico nacional"
    assert normalize_team_name("  Real   Madrid  ") == "real madrid"
