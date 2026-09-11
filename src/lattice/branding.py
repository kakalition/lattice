"""Lattice brand assets (logo paths and helpers)."""

from __future__ import annotations

from pathlib import Path

from lattice.paths import project_root

BRAND_NAME = "Lattice"
# Unicode diamond matching the mark when an image isn't practical.
BRAND_GLYPH = "◇"

_ASSETS = Path(__file__).resolve().parent / "assets"


def logo_path(*, mark: bool = False, dark: bool = False) -> Path:
    """Resolve the Lattice logo file.

    - Full art: textured diamond (default)
    - ``mark``: transparent diamond for footers/chrome
    - ``dark``: near-black mark for light backgrounds
    """
    if mark and dark:
        names = ("logo-mark-dark.png", "logo-mark.png", "logo.png")
    elif mark:
        names = ("logo-mark.png", "logo.png")
    else:
        names = ("logo.png",)

    for name in names:
        candidate = _ASSETS / name
        if candidate.is_file():
            return candidate

    # Repo-root fallback (dev checkout).
    root = project_root() / "logo.png"
    if root.is_file():
        return root
    raise FileNotFoundError("Lattice logo not found (package assets or ./logo.png)")


def brand_label(*, glyph: bool = True) -> str:
    return f"{BRAND_GLYPH} {BRAND_NAME}" if glyph else BRAND_NAME
