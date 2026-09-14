"""OKLCH / shadcn theme tokens for charts and PDF chrome."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict


def oklch_to_hex(L: float, C: float, H: float, *, alpha: float = 1.0) -> str:
    """Convert OKLCH to #RRGGBB (or #RRGGBBAA if alpha < 1)."""
    h = math.radians(H)
    a = C * math.cos(h)
    b = C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l_cube = l_**3
    m = m_**3
    s = s_**3

    r = +4.0767416621 * l_cube - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l_cube + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l_cube - 0.7034186147 * m + 1.7076147010 * s

    def _srgb(x: float) -> int:
        x = max(0.0, min(1.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * (x ** (1 / 2.4)) - 0.055
        return int(round(max(0.0, min(1.0, x)) * 255))

    rr, gg, bb = _srgb(r), _srgb(g), _srgb(bl)
    if alpha >= 1.0:
        return f"#{rr:02X}{gg:02X}{bb:02X}"
    aa = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"#{rr:02X}{gg:02X}{bb:02X}{aa:02X}"


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    if len(c) == 8:
        c = c[:6]
    return tuple(int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def hex_to_rgb255(color: str) -> tuple[int, int, int]:
    r, g, b = hex_to_rgb(color)
    return int(r * 255), int(g * 255), int(b * 255)


class ShadcnTheme(BaseModel):
    model_config = ConfigDict(frozen=True)

    background: str
    foreground: str
    card: str
    card_foreground: str
    muted: str
    muted_foreground: str
    border: str
    primary: str
    charts: tuple[str, str, str, str, str]


# Default shadcn/ui chart tokens (light + dark).
LIGHT = ShadcnTheme(
    background=oklch_to_hex(1.0, 0.0, 0.0),
    foreground=oklch_to_hex(0.145, 0.0, 0.0),
    card=oklch_to_hex(1.0, 0.0, 0.0),
    card_foreground=oklch_to_hex(0.145, 0.0, 0.0),
    muted=oklch_to_hex(0.97, 0.0, 0.0),
    muted_foreground=oklch_to_hex(0.556, 0.0, 0.0),
    border=oklch_to_hex(0.922, 0.0, 0.0),
    primary=oklch_to_hex(0.205, 0.0, 0.0),
    charts=(
        oklch_to_hex(0.646, 0.222, 41.116),
        oklch_to_hex(0.6, 0.118, 184.704),
        oklch_to_hex(0.398, 0.07, 227.392),
        oklch_to_hex(0.828, 0.189, 84.429),
        oklch_to_hex(0.769, 0.188, 70.08),
    ),
)

DARK = ShadcnTheme(
    background=oklch_to_hex(0.145, 0.0, 0.0),
    foreground=oklch_to_hex(0.985, 0.0, 0.0),
    card=oklch_to_hex(0.205, 0.0, 0.0),
    card_foreground=oklch_to_hex(0.985, 0.0, 0.0),
    muted=oklch_to_hex(0.269, 0.0, 0.0),
    muted_foreground=oklch_to_hex(0.708, 0.0, 0.0),
    border=oklch_to_hex(0.3, 0.0, 0.0),
    primary=oklch_to_hex(0.922, 0.0, 0.0),
    charts=(
        oklch_to_hex(0.488, 0.243, 264.376),
        oklch_to_hex(0.696, 0.17, 162.48),
        oklch_to_hex(0.769, 0.188, 70.08),
        oklch_to_hex(0.627, 0.265, 303.9),
        oklch_to_hex(0.645, 0.246, 16.439),
    ),
)


def resolve_theme(name: str) -> ShadcnTheme:
    return DARK if (name or "light").strip().lower() == "dark" else LIGHT
