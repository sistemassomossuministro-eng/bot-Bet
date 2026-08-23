import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.kelly import BankrollLimits
from valuebet.models import BookmakerMarket, Event, Outcome
from valuebet.value_finder import find_value_bets, find_value_bets_in_event


def make_event() -> Event:
    """Evento simulado: Pinnacle (referencia) vs Betplay (objetivo) en 1x2."""
    pinnacle_market = BookmakerMarket(
        bookmaker="Pinnacle",
        market_key="h2h",
        updated_at=datetime.utcnow(),
        outcomes=[
            Outcome("home", 1.95),
            Outcome("draw", 3.60),
            Outcome("away", 4.20),
        ],
    )
    # Betplay ofrece una cuota mejor de lo "justo" en 'home' -> value bet esperado
    betplay_market = BookmakerMarket(
        bookmaker="Betplay",
        market_key="h2h",
        updated_at=datetime.utcnow(),
        outcomes=[
            Outcome("home", 2.20),  # cuota inflada respecto a la referencia
            Outcome("draw", 3.30),
            Outcome("away", 3.80),
        ],
    )
    return Event(
        event_id="evt1",
        sport="football",
        league="Primera A",
        home_team="Millonarios",
        away_team="Nacional",
        commence_time=datetime.utcnow() + timedelta(days=1),
        bookmakers={"Pinnacle": [pinnacle_market], "Betplay": [betplay_market]},
    )


def test_find_value_bets_in_event_detects_positive_ev():
    event = make_event()
    results = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
    )
    assert len(results) == 1
    vb = results[0]
    assert vb.selection == "home"
    assert vb.bookmaker == "Betplay"
    assert vb.ev_pct > 1.0


def test_find_value_bets_in_event_no_value_when_odds_equal():
    event = make_event()
    # Igualamos las cuotas de Betplay a las de Pinnacle -> no debería haber EV positivo notable
    for m in event.bookmakers["Betplay"]:
        for o in m.outcomes:
            o.price_decimal = event.markets_for("Pinnacle", "h2h")[0].outcome_price(o.name)
    results = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
    )
    assert results == []


def test_find_value_bets_applies_kelly_stake_and_daily_loss_limit():
    event = make_event()
    limits = BankrollLimits(total=1_000_000, daily_loss_limit_pct=0.05)

    # Sin pérdida diaria: debería sugerir un stake > 0
    results = find_value_bets(
        [event],
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        min_ev_pct=1.0,
        limits=limits,
        pnl_today=0.0,
    )
    assert len(results) == 1
    assert results[0].suggested_stake and results[0].suggested_stake > 0

    # Con el límite diario de pérdida ya alcanzado: no debe reportar la oportunidad como accionable
    results_after_loss_limit = find_value_bets(
        [event],
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        min_ev_pct=1.0,
        limits=limits,
        pnl_today=-60_000,
    )
    assert results_after_loss_limit == []


def test_find_value_bets_sorted_by_ev_desc():
    event = make_event()
    results = find_value_bets(
        [event],
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        min_ev_pct=0.0,
    )
    evs = [r.ev_pct for r in results]
    assert evs == sorted(evs, reverse=True)


def test_allowed_markets_excludes_market_not_in_list():
    """Con allowed_markets=['h2h'] (el default real en producción), un mercado
    'totals' presente en los datos no debe generar ningún pick, aunque tenga
    EV positivo — ver ValueDetectionConfig.allowed_markets."""
    event = make_event()
    event.bookmakers["Pinnacle"].append(
        BookmakerMarket(
            bookmaker="Pinnacle",
            market_key="totals",
            updated_at=datetime.utcnow(),
            outcomes=[Outcome("over_2.5", 1.90), Outcome("under_2.5", 1.90)],
        )
    )
    event.bookmakers["Betplay"].append(
        BookmakerMarket(
            bookmaker="Betplay",
            market_key="totals",
            updated_at=datetime.utcnow(),
            outcomes=[Outcome("over_2.5", 2.50), Outcome("under_2.5", 1.60)],
        )
    )

    results_unrestricted = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
    )
    # Sin restricción, el 'totals' inflado sí debería colarse (confirma el fixture).
    assert any(vb.market_key == "totals" for vb in results_unrestricted)

    results_h2h_only = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
        allowed_markets=["h2h"],
    )
    assert all(vb.market_key == "h2h" for vb in results_h2h_only)
    assert any(vb.market_key == "totals" for vb in results_h2h_only) is False


def test_max_ev_pct_rejects_implausible_ev():
    """Reproduce el bug real de producción: dos líneas de 'totals' de puntos
    distintos que, por un error de parseo, colapsan al mismo nombre de
    resultado ('over'/'under' sin punto) — el cruce produce un EV disparatado
    que max_ev_pct debe descartar en vez de dejarlo pasar como si fuera una
    oportunidad real."""
    pinnacle_totals = BookmakerMarket(
        bookmaker="Pinnacle",
        market_key="totals",
        updated_at=datetime.utcnow(),
        # Línea de 2.5 goles (probable) mal etiquetada sin punto, por el bug.
        outcomes=[Outcome("over", 1.90), Outcome("under", 1.90)],
    )
    betplay_totals = BookmakerMarket(
        bookmaker="Betplay",
        market_key="totals",
        updated_at=datetime.utcnow(),
        # Línea de 8.5 goles (rarísima) mal etiquetada igual, sin punto.
        outcomes=[Outcome("over", 1.90), Outcome("under", 10.00)],
    )
    event = Event(
        event_id="evt2",
        sport="football",
        league="Liga MX",
        home_team="Puebla",
        away_team="Santos Laguna",
        commence_time=datetime.utcnow() + timedelta(days=1),
        bookmakers={"Pinnacle": [pinnacle_totals], "Betplay": [betplay_totals]},
    )

    results_no_cap = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
    )
    # Confirma que el fixture reproduce el problema: EV disparatado sin el tope.
    assert any(vb.ev_pct > 100 for vb in results_no_cap)

    results_capped = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
        max_ev_pct=50.0,
    )
    assert all(vb.ev_pct <= 50.0 for vb in results_capped)


def test_max_totals_point_rejects_extreme_lines():
    """Caso real reportado por el usuario: 'más de 8.5 goles' con EV positivo
    y bien calculado (sin cruce de líneas — el punto sí calza entre Betplay y
    Pinnacle). Aun así, una línea tan extrema/poco apostada es una referencia
    menos confiable incluso viniendo de un libro 'sharp' — max_totals_point
    la descarta por precaución, no por un bug de cálculo."""
    pinnacle_totals = BookmakerMarket(
        bookmaker="Pinnacle",
        market_key="totals",
        updated_at=datetime.utcnow(),
        outcomes=[Outcome("over_8.5", 2.80), Outcome("under_8.5", 1.45)],
    )
    betplay_totals = BookmakerMarket(
        bookmaker="Betplay",
        market_key="totals",
        updated_at=datetime.utcnow(),
        outcomes=[Outcome("over_8.5", 3.10), Outcome("under_8.5", 1.40)],
    )
    event = Event(
        event_id="evt3",
        sport="football",
        league="DFB Pokal",
        home_team="VfB 1921 Krieschow",
        away_team="FSV Mainz",
        commence_time=datetime.utcnow() + timedelta(days=1),
        bookmakers={"Pinnacle": [pinnacle_totals], "Betplay": [betplay_totals]},
    )

    results_no_cap = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
    )
    # Confirma que el fixture sí produce un pick real (EV correcto, no cruzado) sin el tope.
    assert any(vb.selection == "over_8.5" for vb in results_no_cap)

    results_capped = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
        max_totals_point=5.5,
    )
    assert results_capped == []


def test_max_totals_point_allows_normal_lines():
    """Una línea normal (2.5) no debe verse afectada por max_totals_point."""
    event = make_event()
    event.bookmakers["Pinnacle"].append(
        BookmakerMarket(
            bookmaker="Pinnacle",
            market_key="totals",
            updated_at=datetime.utcnow(),
            outcomes=[Outcome("over_2.5", 1.90), Outcome("under_2.5", 1.90)],
        )
    )
    event.bookmakers["Betplay"].append(
        BookmakerMarket(
            bookmaker="Betplay",
            market_key="totals",
            updated_at=datetime.utcnow(),
            outcomes=[Outcome("over_2.5", 2.50), Outcome("under_2.5", 1.60)],
        )
    )

    results = find_value_bets_in_event(
        event,
        target_bookmakers=["Betplay"],
        reference_bookmakers=["Pinnacle"],
        devig_method="multiplicative",
        min_ev_pct=1.0,
        min_reference_books=1,
        max_totals_point=5.5,
    )
    assert any(vb.selection == "over_2.5" for vb in results)
