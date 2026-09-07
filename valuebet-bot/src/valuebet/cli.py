"""CLI para gestionar el ciclo de vida de las apuestas sugeridas.

El flujo pensado es:
  1. El bot detecta una value bet y te avisa por Telegram (o revisas `list-pending`).
  2. TÚ decides si la colocas y la colocas manualmente en Wplay/Betplay.
  3. Registras lo que realmente hiciste con `confirm` o `reject`.
  4. Cuando el evento termina, registras el resultado con `settle`.

Ejemplos:
    python -m valuebet.cli list-pending
    python -m valuebet.cli confirm 12 --stake 20000
    python -m valuebet.cli reject 12
    python -m valuebet.cli settle 12 --result won --pnl 18000
    python -m valuebet.cli stats
"""
from __future__ import annotations

import argparse

from .config import load_config
from .descriptions import describe_selection
from .storage.db import Storage


def cmd_list_pending(storage: Storage, args) -> None:
    rows = storage.list_pending()
    if not rows:
        print("No hay apuestas pendientes.")
        return
    for r in rows:
        desc = describe_selection(r["market_key"], r["selection"], r["home_team"], r["away_team"])
        print(
            f"#{r['id']:<4} EV {r['ev_pct']:6.2f}%  {r['bookmaker']:<10} @ {r['offered_odds']:.2f}  "
            f"stake_sugerido={r['suggested_stake']}  {r['event_label']}\n"
            f"      → {desc}"
        )


def cmd_confirm(storage: Storage, args) -> None:
    storage.confirm(args.bet_id, args.stake)
    print(f"Apuesta #{args.bet_id} marcada como confirmada con stake={args.stake}.")


def cmd_reject(storage: Storage, args) -> None:
    storage.reject(args.bet_id)
    print(f"Apuesta #{args.bet_id} marcada como rechazada.")


def cmd_settle(storage: Storage, args) -> None:
    storage.settle(args.bet_id, args.result, args.pnl)
    print(f"Apuesta #{args.bet_id} liquidada: {args.result}, PnL={args.pnl}")


def cmd_stats(storage: Storage, args) -> None:
    s = storage.stats()
    print(f"Apuestas liquidadas: {s['settled_count']}")
    print(f"Total apostado: {s['total_staked']:.2f}")
    print(f"PnL total: {s['total_pnl']:.2f}")
    print(f"ROI: {s['roi_pct']:.2f}%")
    print(f"Pendientes: {s['pending_count']}")


def main():
    parser = argparse.ArgumentParser(description="Gestión de apuestas de valor sugeridas")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-pending").set_defaults(func=cmd_list_pending)

    p_confirm = sub.add_parser("confirm", help="Marcar que colocaste la apuesta manualmente")
    p_confirm.add_argument("bet_id", type=int)
    p_confirm.add_argument("--stake", type=float, required=True, help="Stake real que colocaste")
    p_confirm.set_defaults(func=cmd_confirm)

    p_reject = sub.add_parser("reject", help="Marcar que decidiste no tomar la apuesta")
    p_reject.add_argument("bet_id", type=int)
    p_reject.set_defaults(func=cmd_reject)

    p_settle = sub.add_parser("settle", help="Registrar el resultado final")
    p_settle.add_argument("bet_id", type=int)
    p_settle.add_argument("--result", choices=["won", "lost", "void"], required=True)
    p_settle.add_argument("--pnl", type=float, required=True, help="Ganancia(+) o pérdida(-) neta")
    p_settle.set_defaults(func=cmd_settle)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = parser.parse_args()
    cfg = load_config(args.config)
    storage = Storage(cfg.db_path)
    args.func(storage, args)


if __name__ == "__main__":
    main()
