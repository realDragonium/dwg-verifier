"""Tier 2 tests. They need the ODA File Converter (skipped, visibly, without it)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dwgcheck import cli, semantic
from tests.conftest import fixture_id, valid_fixtures
from tools.corruptions import CLASSES, variants


FIXTURES = valid_fixtures()


@pytest.mark.deep
@pytest.mark.parametrize("path", FIXTURES, ids=fixture_id)
def test_valid_fixture_passes_tier2(path: Path, converter: semantic.Converter) -> None:
    assert semantic.check_file(path, converter) == []


@pytest.mark.deep
def test_resealed_object_edit_is_caught_by_tier2(tmp_path: Path, converter: semantic.Converter) -> None:
    cls = tuple(c for c in CLASSES if c.tier1_invisible)
    for v in variants(FIXTURES[0].read_bytes(), FIXTURES[0].name, cls):
        target = tmp_path / f"{v.corruption.name}{v.index}.dwg"
        target.write_bytes(v.data)
        findings = semantic.check_file(target, converter)
        assert findings and all(f.tier == 2 for f in findings)


@pytest.mark.deep
def test_deep_report_attributes_findings_to_both_tiers(tmp_path: Path, converter: semantic.Converter) -> None:
    data = FIXTURES[0].read_bytes()
    target = tmp_path / "truncated.dwg"
    target.write_bytes(data[: len(data) // 2])
    report = cli.check_path(target, converter)
    assert report.tiers_run == [1, 2]
    assert {f.tier for f in report.findings} == {1, 2}
    assert target.read_bytes() == data[: len(data) // 2], "input must never be modified"


def test_offscreen_plugin_detection(tmp_path: Path) -> None:
    exe = tmp_path / "X.app" / "Contents" / "MacOS" / "ODAFileConverter"
    platforms = tmp_path / "X.app" / "Contents" / "PlugIns" / "platforms"
    exe.parent.mkdir(parents=True)
    platforms.mkdir(parents=True)
    exe.touch()
    (platforms / "libqcocoa.dylib").touch()
    assert not semantic._has_offscreen_plugin(exe)
    (platforms / "libqoffscreen.dylib").touch()
    assert semantic._has_offscreen_plugin(exe)
