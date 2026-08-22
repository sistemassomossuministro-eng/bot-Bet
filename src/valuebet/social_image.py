"""Genera piezas de imagen (PNG, 1080x1080) para redes sociales:
- picks del día ("PRONÓSTICOS DEL DÍA")
- resultados del día anterior ("RESULTADOS DE AYER")

Usa Pillow puro (sin navegador ni dependencias pesadas) para correr bien en un
runner de GitHub Actions. Evita emojis dentro de la imagen: DejaVu Sans (la
fuente que se busca por defecto) no trae glyphs de emoji y se verían como
cuadros vacíos — en su lugar se usan "pills" de color con texto (GANADA/
PERDIDA/PUSH/PENDIENTE). Los mensajes de Telegram sí pueden llevar emoji
normalmente, porque esos los renderiza el cliente de Telegram, no Pillow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from .branding import draw_lockup

logger = logging.getLogger(__name__)

WIDTH = HEIGHT = 1080

BG_TOP = (12, 24, 38)
BG_BOTTOM = (8, 36, 30)
HEADER_ACCENT = (56, 217, 141)
TEXT_PRIMARY = (240, 244, 247)
TEXT_MUTED = (148, 163, 176)
DIVIDER = (36, 52, 64)

BADGE_WON = (34, 139, 87)
BADGE_LOST = (176, 48, 48)
BADGE_PUSH = (120, 120, 120)
BADGE_PENDING = (90, 100, 112)
BADGE_EV = (30, 92, 63)

_FONT_DIRS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]
_FONT_DIRS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
]


def _load_font(candidates: Sequence[str], size: int) -> ImageFont.ImageFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # pragma: no cover
                continue
    logger.warning("No se encontró fuente TrueType (%s); usando la fuente por defecto de Pillow.", candidates[0])
    return ImageFont.load_default()


@dataclass
class Fonts:
    title: ImageFont.ImageFont
    subtitle: ImageFont.ImageFont
    row_main: ImageFont.ImageFont
    row_sub: ImageFont.ImageFont
    badge: ImageFont.ImageFont
    footer: ImageFont.ImageFont
    banner: ImageFont.ImageFont
    banner_sub: ImageFont.ImageFont
    stat_value: ImageFont.ImageFont
    stat_label: ImageFont.ImageFont

    @classmethod
    def load(cls) -> "Fonts":
        return cls(
            title=_load_font(_FONT_DIRS_BOLD, 54),
            subtitle=_load_font(_FONT_DIRS_REGULAR, 30),
            row_main=_load_font(_FONT_DIRS_BOLD, 30),
            row_sub=_load_font(_FONT_DIRS_REGULAR, 24),
            badge=_load_font(_FONT_DIRS_BOLD, 22),
            footer=_load_font(_FONT_DIRS_REGULAR, 20),
            banner=_load_font(_FONT_DIRS_BOLD, 40),
            banner_sub=_load_font(_FONT_DIRS_REGULAR, 26),
            stat_value=_load_font(_FONT_DIRS_BOLD, 60),   # hero figure de cada stat tile, >=48px
            stat_label=_load_font(_FONT_DIRS_REGULAR, 22),
        )


@dataclass
class PickRow:
    match_label: str
    detail: str          # ej. "1X2 · Gana Local @2.20"
    right_text: str       # ej. "+12.3% EV" o "GANADA"
    right_color: tuple = BADGE_EV


@dataclass
class StatTile:
    """Un KPI del resumen mensual: 'label' describe el número, 'value' ya viene
    formateado para mostrarse tal cual (ver marks-and-anatomy.md del skill de
    dataviz: value en cifras proporcionales, no tabulares, en el mismo sans)."""
    label: str
    value: str
    value_color: tuple = TEXT_PRIMARY


def _vertical_gradient(img: Image.Image, top: tuple, bottom: tuple) -> None:
    px = img.load()
    for y in range(img.height):
        t = y / max(img.height - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(img.width):
            px[x, y] = (r, g, b)


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if draw.textlength(candidate, font=font) <= max_width:
            lo = mid + 1
        else:
            hi = mid
    return text[: max(lo - 1, 0)].rstrip() + ellipsis


def _draw_pill(
    draw: ImageDraw.ImageDraw, x_right: int, y_center: int, text: str, font: ImageFont.ImageFont, color: tuple
) -> None:
    pad_x, pad_y = 16, 8
    text_w = draw.textlength(text, font=font)
    text_h = font.size
    x1 = x_right
    x0 = x1 - text_w - 2 * pad_x
    y0 = y_center - text_h / 2 - pad_y
    y1 = y_center + text_h / 2 + pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=int((y1 - y0) / 2), fill=color)
    draw.text((x0 + pad_x, y0 + pad_y - 2), text, font=font, fill=TEXT_PRIMARY)


def _base_canvas(fonts: Fonts, title: str, subtitle: str) -> tuple:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    _vertical_gradient(img, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH, 10], fill=HEADER_ACCENT)
    # Logo de marca (ícono BotBet + nombre + tagline) en vez del texto plano
    # que había antes — ver branding.py.
    draw_lockup(img, draw, fonts.row_main, fonts.footer, x=56, y=36, icon_size=46)
    draw.text((56, 110), title, font=fonts.title, fill=TEXT_PRIMARY)
    draw.text((56, 174), subtitle, font=fonts.subtitle, fill=TEXT_MUTED)
    draw.line([(56, 220), (WIDTH - 56, 220)], fill=DIVIDER, width=2)

    return img, draw


def _draw_rows(draw: ImageDraw.ImageDraw, fonts: Fonts, rows: List[PickRow], top: int, bottom: int) -> None:
    if not rows:
        draw.text((56, top + 20), "No hubo picks que cumplieran el EV mínimo hoy.", font=fonts.row_sub, fill=TEXT_MUTED)
        return

    row_h = (bottom - top) / len(rows)
    left = 56
    right = WIDTH - 56

    for i, row in enumerate(rows):
        y0 = top + i * row_h
        y_center = y0 + row_h / 2

        # Número de orden
        draw.text((left, y_center - 18), f"{i + 1:>2}.", font=fonts.row_main, fill=TEXT_MUTED)

        text_left = left + 60
        pill_w_estimate = 170
        max_text_width = right - text_left - pill_w_estimate

        match_line = _truncate(draw, row.match_label, fonts.row_main, max_text_width)
        detail_line = _truncate(draw, row.detail, fonts.row_sub, max_text_width)

        draw.text((text_left, y0 + row_h * 0.18), match_line, font=fonts.row_main, fill=TEXT_PRIMARY)
        draw.text((text_left, y0 + row_h * 0.54), detail_line, font=fonts.row_sub, fill=TEXT_MUTED)

        _draw_pill(draw, right, y_center, row.right_text, fonts.badge, row.right_color)

        if i < len(rows) - 1:
            draw.line([(left, y0 + row_h), (right, y0 + row_h)], fill=DIVIDER, width=1)


def _draw_profitability_banner(
    draw: ImageDraw.ImageDraw, fonts: Fonts, top: int, height: int, is_profitable: bool, headline: str, subline: str
) -> None:
    """Banner de estado (rentable/no rentable) — color = status, reservado y
    nunca reutilizado para otra cosa (mismo verde/rojo que GANADA/PERDIDA)."""
    left, right = 56, WIDTH - 56
    color = BADGE_WON if is_profitable else BADGE_LOST
    draw.rounded_rectangle([left, top, right, top + height], radius=20, fill=color)

    headline_w = draw.textlength(headline, font=fonts.banner)
    subline_w = draw.textlength(subline, font=fonts.banner_sub)
    cx = (left + right) / 2
    draw.text((cx - headline_w / 2, top + height * 0.22), headline, font=fonts.banner, fill=TEXT_PRIMARY)
    draw.text((cx - subline_w / 2, top + height * 0.60), subline, font=fonts.banner_sub, fill=TEXT_PRIMARY)


def _draw_stat_grid(
    draw: ImageDraw.ImageDraw, fonts: Fonts, tiles: List[StatTile], top: int, bottom: int, columns: int = 3
) -> None:
    """Grilla de stat tiles (label + hero number), como un KPI row — ver
    choosing-a-form.md del skill de dataviz: 'un puñado de números clave' es
    exactamente el caso de uso de esta forma, no un gráfico de barras."""
    left, right = 56, WIDTH - 56
    rows = -(-len(tiles) // columns)  # ceil
    cell_w = (right - left) / columns
    cell_h = (bottom - top) / max(rows, 1)

    for i, tile in enumerate(tiles):
        col = i % columns
        row = i // columns
        x0 = left + col * cell_w
        y0 = top + row * cell_h
        cx = x0 + cell_w / 2

        value_w = draw.textlength(tile.value, font=fonts.stat_value)
        draw.text((cx - value_w / 2, y0 + cell_h * 0.18), tile.value, font=fonts.stat_value, fill=tile.value_color)

        label_w = draw.textlength(tile.label, font=fonts.stat_label)
        draw.text((cx - label_w / 2, y0 + cell_h * 0.68), tile.label, font=fonts.stat_label, fill=TEXT_MUTED)

        # Separadores sutiles entre celdas (recessive, como pide el skill).
        if col < columns - 1:
            draw.line([(x0 + cell_w, y0 + cell_h * 0.15), (x0 + cell_w, y0 + cell_h * 0.85)], fill=DIVIDER, width=1)
        if row < rows - 1:
            draw.line([(x0 + 20, y0 + cell_h), (x0 + cell_w - 20, y0 + cell_h)], fill=DIVIDER, width=1)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Envuelve por palabras a como mucho el ancho disponible. No hace falta
    para textos cortos (una sola línea), pero el footer del resumen mensual
    es más largo que el de las otras piezas y no cabe en una sola línea."""
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_footer(draw: ImageDraw.ImageDraw, fonts: Fonts, text: str) -> None:
    max_width = WIDTH - 112
    lines = _wrap_text(draw, text, fonts.footer, max_width)
    line_h = fonts.footer.size + 8
    divider_y = HEIGHT - 34 - line_h * len(lines) - 14
    draw.line([(56, divider_y), (WIDTH - 56, divider_y)], fill=DIVIDER, width=1)
    y = divider_y + 22
    for line in lines:
        draw.text((56, y), line, font=fonts.footer, fill=TEXT_MUTED)
        y += line_h


DISCLAIMER = "Análisis estadístico automatizado, no es garantía de resultado. Juega con responsabilidad. +18."


def render_picks_image(pick_date_str: str, rows: List[PickRow], output_path: str) -> str:
    fonts = Fonts.load()
    img, draw = _base_canvas(fonts, "PRONÓSTICOS DEL DÍA", f"Fútbol · {pick_date_str}")
    _draw_rows(draw, fonts, rows, top=234, bottom=HEIGHT - 110)
    _draw_footer(draw, fonts, DISCLAIMER)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def render_results_image(pick_date_str: str, rows: List[PickRow], summary_line: str, output_path: str) -> str:
    fonts = Fonts.load()
    img, draw = _base_canvas(fonts, "RESULTADOS DE AYER", f"{pick_date_str}  ·  {summary_line}")
    _draw_rows(draw, fonts, rows, top=234, bottom=HEIGHT - 110)
    _draw_footer(draw, fonts, DISCLAIMER)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def render_monthly_summary_image(month_label: str, tiles: List[StatTile], is_profitable: bool, output_path: str) -> str:
    """Resumen mensual: banner de RENTABLE/NO RENTABLE + grilla de KPIs
    (total de picks, ganados, perdidos, etc.). `tiles` ya viene armado por
    `monthly.py` — este módulo solo dibuja."""
    fonts = Fonts.load()
    img, draw = _base_canvas(fonts, "RESUMEN MENSUAL", month_label)

    banner_top, banner_h = 234, 130
    headline = "MES RENTABLE" if is_profitable else "MES NO RENTABLE"
    profit_tile = next((t for t in tiles if t.label.startswith("Profit")), None)
    subline = profit_tile.value if profit_tile else ""
    _draw_profitability_banner(draw, fonts, banner_top, banner_h, is_profitable, headline, subline)

    grid_top = banner_top + banner_h + 40
    _draw_stat_grid(draw, fonts, tiles, top=grid_top, bottom=HEIGHT - 110, columns=3)

    _draw_footer(
        draw,
        fonts,
        "Cálculo con stake plano de 1 unidad por pick (no refleja tu banca real). "
        "Análisis estadístico, no es garantía de resultado futuro.",
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def result_badge(result: str) -> tuple:
    return {
        "won": (BADGE_WON, "GANADA"),
        "lost": (BADGE_LOST, "PERDIDA"),
        "push": (BADGE_PUSH, "ANULADA"),
        "pending": (BADGE_PENDING, "PENDIENTE"),
        "unsupported": (BADGE_PENDING, "S/D"),
        "unsettled_expired": (BADGE_PUSH, "S/D"),
    }.get(result, (BADGE_PENDING, result.upper()))
