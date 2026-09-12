"""Shared pytest setup.

Point the root file handler at a throwaway directory so a test run never
appends to the real ``.lattice/logs/lattice.log``.
"""

from __future__ import annotations

from typing import Any

import pytest

from lattice.logging_config import setup_logging
from lattice.paths import lattice_home


@pytest.fixture(scope="session", autouse=True)
def _isolated_logging(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    prod = lattice_home() / "logs" / "lattice.log"
    prod_mtime = prod.stat().st_mtime if prod.is_file() else None
    prod_size = prod.stat().st_size if prod.is_file() else None
    log_home = tmp_path_factory.mktemp("lattice-log-home")
    setup_logging(home=log_home, force=True, also_stderr=False)
    return {"prod": prod, "mtime": prod_mtime, "size": prod_size, "home": log_home}
