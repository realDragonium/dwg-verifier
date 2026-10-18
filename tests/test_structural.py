from __future__ import annotations

import struct
from functools import cache
from pathlib import Path

import pytest

from dwgcheck.structural import PageEntry, PageRef, SectionInfo, _check_data_page, check_bytes
from tests.conftest import fixture_id, valid_fixtures
from tools.corruptions import CLASSES, Variant, variants

FIXTURES = valid_fixtures()

# The check family each corruption class must trip first.
EXPECTED_CHECK = {
    "truncate_10": "T1.header.copy",
    "truncate_50": "T1.header.copy",
    "truncate_90": "T1.header.copy",
    "truncate_99": "T1.header.copy",
    "flip_header": "T1.header.",
    "flip_page_map": "T1.pagemap.",
    "flip_section_map": "T1.sectionmap.",
    "flip_data_page": "T1.page.",
    "zero_data_page": "T1.page.",
    "append_garbage": "T1.trailing",
}


@cache
def _variants(path: Path) -> list[Variant]:
    return variants(path.read_bytes(), path.name)


@pytest.mark.parametrize("path", FIXTURES, ids=fixture_id)
def test_valid_fixture_has_no_tier1_findings(path: Path) -> None:
    result = check_bytes(path.read_bytes())
    assert result.unsupported is None
    assert result.findings == []


@pytest.mark.parametrize("path", FIXTURES, ids=fixture_id)
def test_every_structural_corruption_is_caught_by_tier1(path: Path) -> None:
    for v in _variants(path):
        if v.corruption.tier1_invisible:
            continue
        findings = check_bytes(v.data).findings
        label = f"{v.corruption.name}#{v.index}"
        assert findings, f"{label} not detected"
        assert all(f.tier == 1 for f in findings)
        assert findings[0].check.startswith(EXPECTED_CHECK[v.corruption.name]), (label, findings[0])


def test_every_corruption_class_has_an_expectation() -> None:
    assert {c.name for c in CLASSES if not c.tier1_invisible} == set(EXPECTED_CHECK)


def test_resealed_object_edit_is_invisible_to_tier1() -> None:
    # Guards the generator: Tier 2 coverage of this class is only meaningful
    # if the file is structurally perfect.
    path = FIXTURES[0]
    for v in _variants(path):
        if v.corruption.tier1_invisible:
            assert check_bytes(v.data).findings == []


def test_truncation_reports_the_offset_where_data_is_missing() -> None:
    data = FIXTURES[0].read_bytes()
    finding = check_bytes(data[: len(data) // 2]).findings[0]
    assert finding.offset is not None and finding.offset > len(data) // 2


@pytest.mark.parametrize("version", [b"AC1018", b"AC1021", b"AC1015", b"\x00\x00\x00\x00\x00\x00"])
def test_other_versions_are_unsupported_not_invalid(version: bytes) -> None:
    result = check_bytes(version + bytes(0x200))
    assert result.unsupported is not None
    assert result.findings == []


def test_file_smaller_than_header_is_invalid() -> None:
    result = check_bytes(b"AC1032" + bytes(10))
    assert [f.check for f in result.findings] == ["T1.size"]


def test_password_encrypted_drawing_is_unsupported() -> None:
    data = bytearray(FIXTURES[0].read_bytes())
    struct.pack_into("<I", data, 0x18, 1)
    assert check_bytes(bytes(data)).unsupported is not None


def test_undersized_page_extent_is_a_finding_not_a_crash() -> None:
    section = SectionInfo("AcDb:Header", 1, 0x7400, 2, (PageRef(1, 0x10, 0),))
    entry = PageEntry(number=1, size=0x10, address=0x100)
    finding = _check_data_page(bytes(0x200), section, section.pages[0], entry, section.name)
    assert finding is not None and finding.check == "T1.page.header"
