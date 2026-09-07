"""Identidad de marca: BotBet — nombre, logo y helpers de dibujo compartidos
por las piezas de redes sociales (social_image.py) y por el ícono de perfil
de Instagram (ver render_profile_icon()).

El logo se dibuja 100% con Pillow (rectángulos, elipses, líneas y texto) —
sin assets externos ni dependencias nuevas — para no depender de nada que no
esté ya en requirements.txt ni de red en tiempo de build/CI.

Concepto: una insignia redondeada en la paleta verde/navy del producto con
una "B" grande, más dos motivos pequeños que explican el nombre:
  - arriba a la derecha, tres nodos conectados por líneas → "Bot" (IA/automatización).
  - abajo, un trazo ascendente tipo gráfico de cuota → "Bet" (valor/ventaja).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

BRAND_NAME = "BotBet"
BRAND_TAGLINE = "PRONÓSTICOS · FÚTBOL MUNDIAL"

# Paleta duplicada (no importada) desde social_image.py a propósito: este
# módulo no depende de social_image.py — es al revés (social_image.py usa
# branding.py para dibujar su cabecera).
BG_TOP = (12, 24, 38)
BG_BOTTOM = (8, 36, 30)
ACCENT = (56, 217, 141)
TEXT_PRIMARY = (240, 244, 247)
EDGE_GREEN = (34, 139, 87)

_FONT_DIRS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_DIRS_BOLD:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # pragma: no cover
                continue
    return ImageFont.load_default()


def _gradient_square(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), BG_TOP)
    px = img.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def render_icon(size: int = 512) -> Image.Image:
    """Devuelve el ícono de marca (RGBA, cuadrado) — insignia redondeada con
    esquinas transparentes, lista para pegarse sobre cualquier canvas o
    exportarse tal cual como foto de perfil."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = max(1, int(size * 0.045))
    radius = int(size * 0.24)

    bg = _gradient_square(size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=255)
    img.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad], radius=radius, outline=ACCENT, width=max(2, size // 70)
    )

    font = _load_font(int(size * 0.52))
    text = "B"
    tw = draw.textlength(text, font=font)
    draw.text((size / 2 - tw / 2, size * 0.20), text, font=font, fill=ACCENT)

    # Motivo "bot": tres nodos conectados, arriba a la derecha.
    node_pts = [(0.72, 0.22), (0.81, 0.30), (0.90, 0.22)]
    abs_pts = [(size * dx, size * dy) for dx, dy in node_pts]
    draw.line(abs_pts, fill=TEXT_PRIMARY, width=max(1, size // 250))
    for cx, cy in abs_pts:
        r = size * 0.014
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=TEXT_PRIMARY)

    # Motivo "bet": trazo ascendente tipo cuota, abajo.
    tick_pts = [(0.28, 0.80), (0.40, 0.70), (0.50, 0.76), (0.70, 0.58)]
    abs_tick = [(size * dx, size * dy) for dx, dy in tick_pts]
    draw.line(abs_tick, fill=EDGE_GREEN, width=max(2, size // 85), joint="curve")
    r2 = size * 0.018
    lx, ly = abs_tick[-1]
    draw.ellipse([lx - r2, ly - r2, lx + r2, ly + r2], fill=EDGE_GREEN)

    return img


def render_profile_icon(output_path: str, size: int = 1024) -> str:
    """Exporta el ícono a PNG cuadrado y opaco, listo para subirlo A MANO como
    foto de perfil de Instagram. La Content Publishing API de Instagram NO
    permite cambiar la foto de perfil por API — eso siempre se hace desde la
    app o instagram.com, una sola vez."""
    icon = render_icon(size)
    canvas = Image.new("RGB", (size, size), BG_TOP)
    canvas.paste(icon, (0, 0), icon)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG", optimize=True)
    return output_path


def draw_lockup(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    name_font: ImageFont.ImageFont,
    tagline_font: ImageFont.ImageFont,
    x: int,
    y: int,
    icon_size: int = 48,
    name_color: tuple = TEXT_PRIMARY,
    tagline_color: tuple = ACCENT,
) -> None:
    """Dibuja el logo completo (ícono + 'BotBet' + tagline) sobre un canvas ya
    existente — es la cabecera de todas las piezas de redes sociales."""
    icon = render_icon(icon_size)
    img.paste(icon, (x, y), icon)
    text_x = x + icon_size + 16
    draw.text((text_x, y - 4), BRAND_NAME, font=name_font, fill=name_color)
    draw.text((text_x, y + icon_size - 22), BRAND_TAGLINE, font=tagline_font, fill=tagline_color)
