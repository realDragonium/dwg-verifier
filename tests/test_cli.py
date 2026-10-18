from __future__ import annotations

import json
from pathlib import Path

import pytest

from dwgcheck import cli
from dwgcheck.report import FileReport, Finding, Status
from tests.conftest import valid_fixtures

VALID = valid_fixtures()[0]


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    data = VALID.read_bytes()
    paths = {
        "valid": tmp_path / "valid.dwg",
        "invalid": tmp_path / "invalid.dwg",
        "unsupported": tmp_path / "old.dwg",
    }
    paths["valid"].write_bytes(data)
    paths["invalid"].write_bytes(data[: len(data) // 2])
    paths["unsupported"].write_bytes(b"AC1015" + bytes(0x200))
    return paths


def _run(tmp_path: Path, names: list[str], files: dict[str, Path], *args: str) -> int:
    target = tmp_path / "set"
    target.mkdir()
    for n in names:
        (target / files[n].name).write_bytes(files[n].read_bytes())
    return cli.main([str(target), *args])


@pytest.mark.parametrize(
    ("names", "code"),
    [
        (["valid"], 0),
        (["valid", "invalid"], 1),
        (["unsupported"], 2),
        (["valid", "unsupported"], 2),
        (["invalid", "unsupported"], 1),
    ],
)
def test_exit_codes(tmp_path: Path, files: dict[str, Path], names: list[str], code: int) -> None:
    assert _run(tmp_path, names, files) == code


def test_missing_target_and_empty_dir_are_environment_errors(tmp_path: Path) -> None:
    assert cli.main([str(tmp_path / "nope.dwg")]) == 3
    assert cli.main([str(tmp_path)]) == 3


def test_recursion_is_opt_in(tmp_path: Path, files: dict[str, Path]) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "x.DWG").write_bytes(files["invalid"].read_bytes())
    assert cli.main([str(tmp_path / "a")]) == 3
    assert cli.main([str(tmp_path / "a"), "-r"]) == 1


def test_json_report_lists_findings_with_location(
    files: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([str(files["invalid"]), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["exit_code"] == 1
    (entry,) = report["files"]
    assert entry["status"] == "invalid" and entry["version"] == "AC1024"
    finding = entry["findings"][0]
    assert finding["file"] == str(files["invalid"])
    assert finding["tier"] == 1
    assert finding["check"].startswith("T1.")
    assert isinstance(finding["offset"], int)


def test_deep_without_converter_fails_loudly(
    files: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DWGCHECK_ODAFC", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("dwgcheck.semantic._candidates", lambda system: [])
    assert cli.main([str(files["valid"]), "--deep"]) == 3
    assert "opendesign.com" in capsys.readouterr().err


def test_human_output_escapes_file_derived_text() -> None:
    report = FileReport(
        "f.dwg",
        Status.INVALID,
        version="AC1032",
        findings=[Finding(tier=1, check="T1.x", message="bad", section="\x1b[2Jevil")],
    )
    assert "\x1b" not in cli.format_human(report)
