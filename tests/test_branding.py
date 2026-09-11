"""Brand asset helpers."""

from __future__ import annotations

from lattice.branding import BRAND_GLYPH, BRAND_NAME, brand_label, logo_path


def test_logo_paths_exist() -> None:
    assert logo_path().is_file()
    assert logo_path(mark=True).is_file()
    assert logo_path(mark=True, dark=True).is_file()


def test_brand_label() -> None:
    assert brand_label() == f"{BRAND_GLYPH} {BRAND_NAME}"
    assert brand_label(glyph=False) == BRAND_NAME
