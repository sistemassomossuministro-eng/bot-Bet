"""Almacenamiento local en SQLite: apuestas sugeridas/confirmadas/liquidadas y banca.

Todo el ciclo de vida de una apuesta pasa por estados explícitos:

  pending    -> el motor la detectó y (opcionalmente) se alertó por Telegram
  confirmed  -> el usuario la colocó manualmente en la casa de apuestas (registra stake real)
  rejected   -> el usuario decidió no tomarla
  settled    -> se conoce el resultado (ganada/perdida/anulada) y se registra el PnL

Nada aquí coloca apuestas; solo lleva la contabilidad de lo que el usuario
decide hacer con las sugerencias.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from ..models import ValueBet

SCHEMA = """
CREATE TABLE IF NOT EXISTS value_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_label TEXT NOT NULL,
    home_team TEXT NOT NULL DEFAULT '',
    away_team TEXT NOT NULL DEFAULT '',
    commence_time TEXT NOT NULL,
    market_key TEXT NOT NULL,
    selection TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    offered_odds REAL NOT NULL,
    fair_probability REAL NOT NULL,
    ev_pct REAL NOT NULL,
    suggested_stake REAL,
    actual_stake REAL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | rejected | settled
    result TEXT,                              -- won | lost | void (solo si status=settled)
    pnl REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, market_key, selection, bookmaker)
);

CREATE TABLE IF NOT EXISTS bankroll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    change REAL NOT NULL,
    reason TEXT,
    balance_after REAL NOT NULL
);

-- Picks del resumen diario (los "10 de la mañana"), independientes de value_bets.
-- Su resultado (won/lost/push) se calcula automáticamente comparando la selección
-- contra el marcador final del partido — no depende de que el usuario haya apostado
-- realmente. Es el histórico de desempeño del modelo, para transparencia y para las
-- piezas de redes sociales.
CREATE TABLE IF NOT EXISTS daily_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_date TEXT NOT NULL,          -- fecha (America/Bogota) en que se generó el pick, YYYY-MM-DD
    event_id TEXT NOT NULL,
    event_label TEXT NOT NULL,
    home_team TEXT NOT NULL DEFAULT '',
    away_team TEXT NOT NULL DEFAULT '',
    commence_time TEXT NOT NULL,
    market_key TEXT NOT NULL,
    selection TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    offered_odds REAL NOT NULL,
    fair_probability REAL NOT NULL,
    ev_pct REAL NOT NULL,
    result TEXT NOT NULL DEFAULT 'pending',  -- pending | won | lost | push | unsupported | unsettled_expired
    home_score INTEGER,
    away_score INTEGER,
    settled_at TEXT,
    created_at TEXT NOT NULL,
    -- closing_odds/closing_captured_at: ver clv.py. odds-api.io no da cuotas
    -- históricas en el plan gratuito, así que este "precio de cierre" se
    -- captura en vivo, poco antes de que arranque el partido, con un
    -- workflow aparte (clv_snapshot.yml) — no siempre va a estar presente
    -- (ej. si el mercado ya cerró para cuando corrió esa captura).
    closing_odds REAL,
    closing_captured_at TEXT,
    UNIQUE(pick_date, event_id, market_key, selection, bookmaker)
);
"""


@dataclass
class Storage:
    db_path: str

    def __post_init__(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """CREATE TABLE IF NOT EXISTS no agrega columnas a una tabla que ya
        existía con un esquema anterior — esto cubre ese caso para quien ya
        tenía una data/valuebet.db corriendo antes de que se agregaran
        home_team/away_team (necesarias para las descripciones legibles)."""
        for table, column, coltype in [
            ("value_bets", "home_team", "TEXT NOT NULL DEFAULT ''"),
            ("value_bets", "away_team", "TEXT NOT NULL DEFAULT ''"),
            ("daily_picks", "home_team", "TEXT NOT NULL DEFAULT ''"),
            ("daily_picks", "away_team", "TEXT NOT NULL DEFAULT ''"),
            ("daily_picks", "closing_odds", "REAL"),
            ("daily_picks", "closing_captured_at", "TEXT"),
        ]:
            existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_pending(self, vb: ValueBet) -> "tuple[int, bool]":
        """Inserta una nueva oportunidad detectada, o actualiza la existente si sigue pendiente.

        Devuelve (bet_id, is_new). is_new=False tanto si ya existía como pendiente
        (se actualizó) como si existe con otro estado (confirmed/rejected/settled),
        caso en el que NO se toca la fila existente — se asume que el usuario ya
        decidió qué hacer con esa oportunidad.
        No usa cursor.lastrowid porque con INSERT ... ON CONFLICT DO UPDATE
        SQLite solo actualiza last_insert_rowid() en el camino de INSERT real,
        no en el de UPDATE, lo que daría el id equivocado en ese caso.
        """
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM value_bets WHERE event_id=? AND market_key=? AND selection=? AND bookmaker=?",
                (vb.event.event_id, vb.market_key, vb.selection, vb.bookmaker),
            ).fetchone()
            is_new = existing is None

            conn.execute(
                """
                INSERT INTO value_bets
                    (event_id, event_label, home_team, away_team, commence_time, market_key, selection,
                     bookmaker, offered_odds, fair_probability, ev_pct, suggested_stake, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(event_id, market_key, selection, bookmaker) DO UPDATE SET
                    offered_odds=excluded.offered_odds,
                    fair_probability=excluded.fair_probability,
                    ev_pct=excluded.ev_pct,
                    suggested_stake=excluded.suggested_stake,
                    updated_at=excluded.updated_at
                WHERE value_bets.status = 'pending'
                """,
                (
                    vb.event.event_id,
                    vb.event.label(),
                    vb.event.home_team,
                    vb.event.away_team,
                    vb.event.commence_time.isoformat(),
                    vb.market_key,
                    vb.selection,
                    vb.bookmaker,
                    vb.offered_odds,
                    vb.fair_probability,
                    vb.ev_pct,
                    vb.suggested_stake,
                    now,
                    now,
                ),
            )

            row = conn.execute(
                "SELECT id FROM value_bets WHERE event_id=? AND market_key=? AND selection=? AND bookmaker=?",
                (vb.event.event_id, vb.market_key, vb.selection, vb.bookmaker),
            ).fetchone()
            return row["id"], is_new

    def list_pending(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM value_bets WHERE status = 'pending' ORDER BY ev_pct DESC"
            ).fetchall()

    def get(self, bet_id: int):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM value_bets WHERE id = ?", (bet_id,)).fetchone()

    def confirm(self, bet_id: int, actual_stake: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE value_bets SET status='confirmed', actual_stake=?, updated_at=? WHERE id=?",
                (actual_stake, datetime.utcnow().isoformat(), bet_id),
            )

    def reject(self, bet_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE value_bets SET status='rejected', updated_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), bet_id),
            )

    def settle(self, bet_id: int, result: str, pnl: float) -> None:
        if result not in ("won", "lost", "void"):
            raise ValueError("result debe ser 'won', 'lost' o 'void'")
        with self._conn() as conn:
            conn.execute(
                "UPDATE value_bets SET status='settled', result=?, pnl=?, updated_at=? WHERE id=?",
                (result, pnl, datetime.utcnow().isoformat(), bet_id),
            )
            row = conn.execute(
                "SELECT COALESCE(SUM(change), 0) AS bal FROM bankroll_log"
            ).fetchone()
            new_balance = row["bal"] + pnl
            conn.execute(
                "INSERT INTO bankroll_log (ts, change, reason, balance_after) VALUES (?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), pnl, f"settle bet #{bet_id} ({result})", new_balance),
            )

    def staked_today(self, today: Optional[date] = None) -> float:
        today = today or datetime.utcnow().date()
        prefix = today.isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(actual_stake), 0) AS s FROM value_bets "
                "WHERE status IN ('confirmed','settled') AND substr(updated_at,1,10) = ?",
                (prefix,),
            ).fetchone()
            return row["s"] or 0.0

    def pnl_today(self, today: Optional[date] = None) -> float:
        today = today or datetime.utcnow().date()
        prefix = today.isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS p FROM value_bets "
                "WHERE status = 'settled' AND substr(updated_at,1,10) = ?",
                (prefix,),
            ).fetchone()
            return row["p"] or 0.0

    def stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='settled') AS settled_count,
                    COALESCE(SUM(pnl) FILTER (WHERE status='settled'), 0) AS total_pnl,
                    COALESCE(SUM(actual_stake) FILTER (WHERE status='settled'), 0) AS total_staked,
                    COUNT(*) FILTER (WHERE status='pending') AS pending_count
                FROM value_bets
                """
            ).fetchone()
            roi = (row["total_pnl"] / row["total_staked"] * 100) if row["total_staked"] else 0.0
            return {
                "settled_count": row["settled_count"],
                "total_pnl": row["total_pnl"],
                "total_staked": row["total_staked"],
                "roi_pct": roi,
                "pending_count": row["pending_count"],
            }

    # ---- daily_picks (resumen diario / redes sociales) ----------------------

    def add_daily_pick(self, pick_date: str, vb: ValueBet) -> Optional[int]:
        """Inserta un pick del resumen diario. Si ya existía (misma fecha+evento+mercado+
        selección+casa) lo deja tal cual (no lo pisa) y devuelve None."""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO daily_picks
                    (pick_date, event_id, event_label, home_team, away_team, commence_time, market_key, selection,
                     bookmaker, offered_odds, fair_probability, ev_pct, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    pick_date,
                    vb.event.event_id,
                    vb.event.label(),
                    vb.event.home_team,
                    vb.event.away_team,
                    vb.event.commence_time.isoformat(),
                    vb.market_key,
                    vb.selection,
                    vb.bookmaker,
                    vb.offered_odds,
                    vb.fair_probability,
                    vb.ev_pct,
                    now,
                ),
            )
            return cur.lastrowid if cur.rowcount > 0 else None

    def get_daily_pick(self, pick_id: int):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM daily_picks WHERE id = ?", (pick_id,)).fetchone()

    def list_picks_for_date(self, pick_date: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM daily_picks WHERE pick_date = ? ORDER BY ev_pct DESC", (pick_date,)
            ).fetchall()

    def list_pending_picks_before(self, pick_date: str):
        """Picks aún 'pending' de fechas anteriores a pick_date (para liquidar)."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM daily_picks WHERE result = 'pending' AND pick_date < ? ORDER BY pick_date",
                (pick_date,),
            ).fetchall()

    def settle_daily_pick(
        self,
        pick_id: int,
        result: str,
        home_score: Optional[int] = None,
        away_score: Optional[int] = None,
    ) -> None:
        if result not in ("won", "lost", "push", "unsupported", "unsettled_expired", "pending"):
            raise ValueError(f"Resultado de pick diario inválido: {result}")
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE daily_picks
                SET result=?, home_score=?, away_score=?, settled_at=?
                WHERE id=?
                """,
                (result, home_score, away_score, datetime.utcnow().isoformat(), pick_id),
            )

    def list_picks_needing_closing_snapshot(self, deadline_iso: str):
        """Picks pendientes cuyo partido arranca antes de `deadline_iso` y que
        todavía no tienen cuota de cierre capturada. Usado por
        capture_closing_snapshots() (ver clv.py)."""
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT * FROM daily_picks
                WHERE result = 'pending' AND closing_odds IS NULL AND commence_time <= ?
                ORDER BY commence_time
                """,
                (deadline_iso,),
            ).fetchall()

    def set_closing_odds(self, pick_id: int, closing_odds: float, captured_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE daily_picks SET closing_odds=?, closing_captured_at=? WHERE id=?",
                (closing_odds, captured_at, pick_id),
            )

    def daily_picks_summary(self, pick_date: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE result='won') AS won,
                    COUNT(*) FILTER (WHERE result='lost') AS lost,
                    COUNT(*) FILTER (WHERE result='push') AS push,
                    COUNT(*) FILTER (WHERE result='pending') AS pending,
                    COALESCE(AVG(ev_pct), 0) AS avg_ev_pct
                FROM daily_picks WHERE pick_date = ?
                """,
                (pick_date,),
            ).fetchone()
            decided = (row["won"] or 0) + (row["lost"] or 0)
            hit_rate = (row["won"] / decided * 100) if decided else None
            return {
                "total": row["total"],
                "won": row["won"],
                "lost": row["lost"],
                "push": row["push"],
                "pending": row["pending"],
                "avg_ev_pct": row["avg_ev_pct"],
                "hit_rate_pct": hit_rate,
            }

    @staticmethod
    def _aggregate_picks(conn: sqlite3.Connection, where_sql: str, params: tuple) -> dict:
        """Agregación compartida por monthly_picks_summary() y
        recent_picks_summary() — mismo cálculo, distinto filtro de fechas.

        La 'rentabilidad' se calcula con un supuesto de STAKE PLANO: se asume
        que cada pick arriesga 1 unidad. Si ganó, la ganancia es (cuota - 1)
        unidades; si perdió, la pérdida es 1 unidad; un push no gana ni pierde.
        Esto es lo estándar en reportes de tipsters/pronosticadores cuando no
        hay un stake real registrado (el stake real de lo que apostaste de
        verdad se lleva aparte, en la tabla value_bets vía la CLI) — no es tu
        rendimiento real de dinero, es el desempeño del MODELO si cada pick se
        hubiera jugado igual.
        """
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result='won') AS won,
                COUNT(*) FILTER (WHERE result='lost') AS lost,
                COUNT(*) FILTER (WHERE result='push') AS push,
                COUNT(*) FILTER (WHERE result NOT IN ('won','lost','push')) AS other,
                COALESCE(AVG(ev_pct), 0) AS avg_ev_pct,
                COALESCE(SUM(CASE
                    WHEN result='won' THEN offered_odds - 1
                    WHEN result='lost' THEN -1
                    ELSE 0
                END), 0) AS profit_units,
                COUNT(*) FILTER (WHERE closing_odds IS NOT NULL) AS clv_sample_size,
                AVG(CASE WHEN closing_odds IS NOT NULL
                    THEN (offered_odds / closing_odds - 1) * 100 END) AS avg_clv_pct,
                COUNT(*) FILTER (WHERE closing_odds IS NOT NULL AND offered_odds > closing_odds) AS clv_positive_count
            FROM daily_picks
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        decided = (row["won"] or 0) + (row["lost"] or 0)
        hit_rate = (row["won"] / decided * 100) if decided else None
        roi = (row["profit_units"] / decided * 100) if decided else None
        clv_sample_size = row["clv_sample_size"] or 0
        clv_positive_rate = (row["clv_positive_count"] / clv_sample_size * 100) if clv_sample_size else None
        return {
            "total": row["total"] or 0,
            "won": row["won"] or 0,
            "lost": row["lost"] or 0,
            "push": row["push"] or 0,
            "other": row["other"] or 0,
            "avg_ev_pct": row["avg_ev_pct"] or 0.0,
            "profit_units": row["profit_units"] or 0.0,
            "hit_rate_pct": hit_rate,
            "roi_pct": roi,
            # CLV = Closing Line Value: ver clv.py. Compara la cuota tomada
            # contra la cuota de cierre capturada poco antes del partido —
            # positivo = conseguiste mejor precio que el mercado al cierre,
            # la señal más confiable de que hay valor real (se puede leer
            # pick por pick, no necesita miles de apuestas como el acierto).
            "clv_sample_size": clv_sample_size,
            "avg_clv_pct": row["avg_clv_pct"],
            "clv_positive_rate_pct": clv_positive_rate,
        }

    def monthly_picks_summary(self, year: int, month: int) -> dict:
        """Resumen del mes calendario (year, month) sobre daily_picks. Ver
        _aggregate_picks() para el detalle de cada campo."""
        prefix = f"{year:04d}-{month:02d}"
        with self._conn() as conn:
            summary = self._aggregate_picks(conn, "substr(pick_date, 1, 7) = ?", (prefix,))
            summary["year"] = year
            summary["month"] = month
            return summary

    def recent_picks_summary(self, days: int, today: Optional[date] = None) -> dict:
        """Resumen de ventana móvil de los últimos `days` días (hasta hoy
        inclusive), a diferencia de monthly_picks_summary() que se resetea el
        día 1 de cada mes. Útil para chequear el estado del bot en cualquier
        momento sin esperar a fin de mes — ver _aggregate_picks()."""
        today = today or datetime.utcnow().date()
        since = (today - timedelta(days=days - 1)).isoformat()
        with self._conn() as conn:
            summary = self._aggregate_picks(conn, "pick_date >= ?", (since,))
            summary["days"] = days
            summary["since"] = since
            return summary
