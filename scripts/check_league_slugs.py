"""Chequeo semanal de slugs de ligas de fútbol (ver README, "Riesgos de
mantenimiento del filtro de fútbol", y src/valuebet/league_slug_check.py para
la lógica completa de matching).

Compara los slugs configurados en `leagues_by_sport.football` de
config.example.yaml contra una respuesta real de GET /leagues?sport=football
y:

- Si un slug configurado ya no existe pero hay EXACTAMENTE una liga viva que
  es claramente la misma competencia con otra fase (le quitaron/cambiaron el
  sufijo, ej. "-apertura" -> "-clausura", o como pasó con la UEFA en
  sep-2026, le quitaron el sufijo del todo) -> actualiza config.example.yaml
  automáticamente, deja un comentario con la fecha, y el workflow que llama a
  este script hace el commit/push.
- Si no hay un reemplazo seguro (0 candidatos, o más de 1) -> NO toca el
  archivo. Solo deja el detalle en el log y, si Telegram está configurado,
  manda un resumen para que un humano decida — nunca se adivina un slug.

Se corre semanalmente vía GitHub Actions
(.github/workflows/weekly_league_slug_check.yml), pero también se puede
correr a mano:

    export ODDS_API_KEY="tu-key-real"
    export TELEGRAM_BOT_TOKEN="..."   # opcional, solo para el resumen
    export TELEGRAM_CHAT_ID="..."     # opcional, solo para el resumen
    python scripts/check_league_slugs.py --config config.example.yaml
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from valuebet.league_slug_check import (
    apply_renames_to_config_text,
    extract_football_league_slugs,
    find_slug_changes,
)
from valuebet.odds_provider import OddsApiIoProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("check_league_slugs")


def _send_telegram_summary(text: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.info(
            "Telegram no configurado (faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — "
            "el resumen solo queda en este log."
        )
        return
    try:
        from valuebet.alerts.telegram_bot import TelegramAlerter

        TelegramAlerter(bot_token, chat_id).send(text)
    except Exception:
        # Nunca debe tumbar el job por esto — el log ya tiene el detalle
        # completo, Telegram es solo una conveniencia adicional.
        logger.exception("No se pudo enviar el resumen por Telegram (no es fatal).")


def run(config_path: Path) -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        logger.error("Falta ODDS_API_KEY en el entorno.")
        return 1

    yaml_text = config_path.read_text(encoding="utf-8")
    configured_slugs = extract_football_league_slugs(yaml_text)
    if not configured_slugs:
        logger.error(
            "No se encontró ningún slug en leagues_by_sport.football de %s — "
            "revisa el formato del archivo antes de confiar en este chequeo.",
            config_path,
        )
        return 1

    provider = OddsApiIoProvider(api_key=api_key)
    live_leagues = provider.list_leagues("football")
    live_slugs = [lg["slug"] for lg in live_leagues if lg.get("slug")]

    confident, ambiguous = find_slug_changes(configured_slugs, live_slugs)

    if confident:
        today = date.today().isoformat()
        new_text = apply_renames_to_config_text(yaml_text, confident, today)
        config_path.write_text(new_text, encoding="utf-8")
        for old, new in confident.items():
            logger.info("Slug actualizado automáticamente: '%s' -> '%s'", old, new)

    for old, candidates in ambiguous.items():
        if candidates:
            logger.warning(
                "Slug '%s' ya no existe y hay %d candidatos ambiguos (no se auto-actualiza): %s",
                old,
                len(candidates),
                candidates,
            )
        else:
            logger.warning(
                "Slug '%s' ya no existe y no se encontró ningún candidato parecido — "
                "revisa GET /leagues?sport=football manualmente.",
                old,
            )

    if confident or ambiguous:
        lines = ["⚙️ <b>BotBet — chequeo semanal de ligas de fútbol</b>"]
        if confident:
            lines.append("\n✅ Slugs actualizados automáticamente:")
            lines += [f"• {old} → {new}" for old, new in confident.items()]
        if ambiguous:
            lines.append("\n⚠️ Necesitan revisión manual (NO se tocó el archivo):")
            for old, candidates in ambiguous.items():
                cand_txt = ", ".join(candidates) if candidates else "sin candidatos parecidos"
                lines.append(f"• {old} → {cand_txt}")
            lines.append("\nRevisa con GET /leagues?sport=football y actualiza config.example.yaml a mano.")
        _send_telegram_summary("\n".join(lines))
    else:
        logger.info(
            "Los %d slugs de fútbol configurados siguen existiendo tal cual en la API. Nada que hacer.",
            len(configured_slugs),
        )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()
    return run(Path(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
