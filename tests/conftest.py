from __future__ import annotations

from pathlib import Path

import pytest

from dwgcheck import semantic

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIRS = (ROOT / "fixtures" / "libredwg", ROOT / "fixtures" / "local")


def valid_fixtures() -> list[Path]:
    return sorted(p for d in FIXTURE_DIRS for p in d.rglob("*") if p.suffix.lower() == ".dwg")


def fixture_id(path: Path) -> str:
    return str(path.relative_to(ROOT / "fixtures"))


@pytest.fixture(scope="session")
def converter() -> semantic.Converter:
    try:
        return semantic.find_converter()
    except semantic.EnvironmentProblem as exc:
        pytest.skip(f"Tier 2 unavailable: {exc}")
