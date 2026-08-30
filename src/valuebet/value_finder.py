"""Motor que combina cuotas + devig + EV + Kelly para producir ValueBet candidatos.

No coloca apuestas ni interactúa con ninguna casa: solo produce recomendaciones
para que una persona decida y las ejecute manualmente.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .devig import expected_value_pct, fair_probabilities
from .kelly import BankrollLimits, suggest_stake
from .models import Event, ValueBet
from .settlement import parse_point_suffix

logger = logging.getLogger(__name__)


@dataclass
class NearMiss:
    """Un candidato que pasó los filtros de cuota (min_odds/max_odds,
    max_totals_point) y se pudo devigar/calcular su EV real, pero NO llegó a
    `min_ev_pct` — o sí llegó, da igual: esto registra el EV real de
    cualquier candidato evaluable, se convierta o no en ValueBet.

    Se agregó (2026-08-29) porque `ev_pct < min_ev_pct: continue` descartaba
    en completo silencio — sin este registro, un día en 0 picks no permite
    distinguir "los candidatos en rango rozaron el mínimo" de "ninguno se
    acercó ni remotamente", así que cualquier ajuste de min_odds/max_odds/
    min_ev_pct se hacía a ciegas. Nunca se usa para decidir un pick ni se le
    muestra al usuario — es solo observabilidad para guiar el próximo ajuste
    de filtros con datos reales en vez de adivinar (ver README, "Rango de
    cuota")."""

    event_label: str
    market_key: str
    selection: str
    bookmaker: str
    offered_odds: float
    ev_pct: float


def _reference_prices(event: Event, reference_bookmakers: List[str], market_key: str, outcome_name: str) -> List[float]:
    """Junta la cuota de un mismo resultado entre los libros de referencia disponibles."""
    prices = []
    for bk in reference_bookmakers:
        for m in event.markets_for(bk, market_key):
            p = m.outcome_price(outcome_name)
            if p:
                prices.append(p)
    return prices


def find_value_bets_in_event(
    event: Event,
    target_bookmakers: List[str],
    reference_bookmakers: List[str],
    devig_method: str,
    min_ev_pct: float,
    min_reference_books: int,
    limits: Optional[BankrollLimits] = None,
    staked_today: float = 0.0,
    pnl_today: float = 0.0,
    allowed_markets: Optional[List[str]] = None,
    max_ev_pct: Optional[float] = None,
    max_totals_point: Optional[float] = None,
    min_odds: Optional[float] = None,
    max_odds: Optional[float] = None,
    near_misses: Optional[List[NearMiss]] = None,
) -> List[ValueBet]:
    results: List[ValueBet] = []

    for target_bk in target_bookmakers:
        for market in event.bookmakers.get(target_bk, []):
            market_key = market.market_key

            if allowed_markets is not None and market_key not in allowed_markets:
                # Mercado no habilitado (ver ValueDetectionConfig.allowed_markets) —
                # por defecto solo h2h, porque totals/spreads todavía no están
                # verificados contra la forma real de la respuesta del proveedor.
                continue

            # Cuotas del libro de referencia para EL MISMO mercado, para poder devigar
            # correctamente (necesitamos todos los resultados del mercado, no solo uno).
            ref_markets = []
            for ref_bk in reference_bookmakers:
                ref_markets.extend(event.markets_for(ref_bk, market_key))
            if len(set(m.bookmaker for m in ref_markets)) < min_reference_books:
                continue

            for outcome in market.outcomes:
                if market_key == "totals" and max_totals_point is not None:
                    _side, point = parse_point_suffix(outcome.name)
                    if point is not None and point > max_totals_point:
                        # Líneas de goles muy extremas (7.5, 8.5, 9.5...) casi no se
                        # apuestan — incluso un libro de referencia "sharp" como
                        # Bet365 les dedica menos cuidado que a la línea principal
                        # (ej. 2.5), así que su cuota ahí es una referencia menos
                        # confiable para calcular la probabilidad "justa". No es el
                        # bug de cruce de líneas (ver max_ev_pct/hdp) — el cálculo
                        # es correcto, pero la fuente de comparación es más débil.
                        # Ver ValueDetectionConfig.max_totals_point.
                        logger.info(
                            "Línea de 'totals' descartada por ser extrema (%.1f > tope %.1f): %s",
                            point,
                            max_totals_point,
                            event.label(),
                        )
                        continue

                if min_odds is not None and outcome.price_decimal < min_odds:
                    # Cuota muy baja: casi nunca hay EV real ahí, y si lo hay
                    # el margen de error es carísimo (ver ValueDetectionConfig.min_odds).
                    continue

                if max_odds is not None and outcome.price_decimal > max_odds:
                    # Resultado poco probable: un error chico en la probabilidad
                    # "justa" estimada se magnifica mucho más en una cuota alta
                    # que en una cercana a evens — el mismo problema de fondo
                    # que max_totals_point, pero para cualquier mercado, no solo
                    # totals (ver ValueDetectionConfig.max_odds y el README,
                    # "Rango de cuota").
                    logger.info(
                        "Cuota descartada por estar fuera del rango configurado (%.2f > tope %.2f): %s · %s · %s (%s)",
                        outcome.price_decimal,
                        max_odds,
                        event.label(),
                        market_key,
                        outcome.name,
                        target_bk,
                    )
                    continue

                ref_prices = _reference_prices(event, reference_bookmakers, market_key, outcome.name)
                if not ref_prices:
                    continue
                avg_ref_price = sum(ref_prices) / len(ref_prices)

                # Necesitamos TODAS las cuotas del mercado de un mismo libro de referencia
                # para devigar bien. Tomamos el primer libro de referencia que tenga el mercado completo.
                full_ref_market = next(
                    (m for m in ref_markets if m.outcome_price(outcome.name) is not None),
                    None,
                )
                if full_ref_market is None or len(full_ref_market.outcomes) < 2:
                    continue

                try:
                    fair_probs = fair_probabilities(
                        [o.price_decimal for o in full_ref_market.outcomes], method=devig_method
                    )
                except ValueError as exc:
                    logger.debug("No se pudo devigar %s: %s", event.label(), exc)
                    continue

                idx = next(
                    (i for i, o in enumerate(full_ref_market.outcomes) if o.name == outcome.name), None
                )
                if idx is None:
                    continue
                fair_prob = fair_probs[idx]

                try:
                    ev_pct = expected_value_pct(outcome.price_decimal, fair_prob)
                except ValueError:
                    continue

                if near_misses is not None:
                    # Se registra ANTES del corte de min_ev_pct a propósito —
                    # ver NearMiss arriba: esto es lo único que permite saber
                    # qué tan cerca (o lejos) estuvo el mejor candidato real
                    # del umbral exigido, en vez de solo saber "0 picks".
                    near_misses.append(
                        NearMiss(
                            event_label=event.label(),
                            market_key=market_key,
                            selection=outcome.name,
                            bookmaker=target_bk,
                            offered_odds=outcome.price_decimal,
                            ev_pct=ev_pct,
                        )
                    )

                if ev_pct < min_ev_pct:
                    continue

                if max_ev_pct is not None and ev_pct > max_ev_pct:
                    # Un EV así de alto casi nunca es valor real — casi siempre es un
                    # bug de datos/parseo (líneas mal emparejadas, cuota corrupta,
                    # etc.). Se descarta y se deja registro en el log en vez de
                    # mostrarlo como si fuera confiable — ver ValueDetectionConfig.max_ev_pct.
                    logger.warning(
                        "EV implausible descartado (%.1f%% > tope %.1f%%): %s · %s · %s @ %.2f (%s)",
                        ev_pct,
                        max_ev_pct,
                        event.label(),
                        market_key,
                        outcome.name,
                        outcome.price_decimal,
                        target_bk,
                    )
                    continue

                vb = ValueBet(
                    event=event,
                    market_key=market_key,
                    selection=outcome.name,
                    bookmaker=target_bk,
                    offered_odds=outcome.price_decimal,
                    fair_probability=fair_prob,
                    ev_pct=ev_pct,
                    reference_bookmakers=[m.bookmaker for m in ref_markets],
                )

                if limits is not None:
                    suggestion = suggest_stake(
                        fair_probability=fair_prob,
                        decimal_odds=outcome.price_decimal,
                        limits=limits,
                        staked_today=staked_today,
                        pnl_today=pnl_today,
                    )
                    vb.suggested_stake = suggestion.stake
                    vb.kelly_fraction_used = suggestion.kelly_fraction_used
                    if suggestion.stake <= 0:
                        # Sin presupuesto o límite diario alcanzado: no la reportamos como accionable.
                        continue

                results.append(vb)

    return results


def find_value_bets(
    events: List[Event],
    target_bookmakers: List[str],
    reference_bookmakers: List[str],
    devig_method: str = "multiplicative",
    min_ev_pct: float = 3.0,
    min_reference_books: int = 1,
    limits: Optional[BankrollLimits] = None,
    staked_today: float = 0.0,
    pnl_today: float = 0.0,
    allowed_markets: Optional[List[str]] = None,
    max_ev_pct: Optional[float] = None,
    max_totals_point: Optional[float] = None,
    min_odds: Optional[float] = None,
    max_odds: Optional[float] = None,
    near_misses: Optional[List[NearMiss]] = None,
) -> List[ValueBet]:
    all_results: List[ValueBet] = []
    for event in events:
        all_results.extend(
            find_value_bets_in_event(
                event,
                target_bookmakers,
                reference_bookmakers,
                devig_method,
                min_ev_pct,
                min_reference_books,
                limits,
                staked_today,
                pnl_today,
                allowed_markets,
                max_ev_pct,
                max_totals_point,
                min_odds,
                max_odds,
                near_misses,
            )
        )
    all_results.sort(key=lambda vb: vb.ev_pct, reverse=True)
    return all_results
