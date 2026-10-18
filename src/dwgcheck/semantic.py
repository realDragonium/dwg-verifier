"""Tier 2: semantic checks via the ODA File Converter and ezdxf's auditor.

ezdxf.addons.odafc.convert() accepts audit=True but discards the converter's
.err report (and may even move the .err file in place of the DXF), so the
converter is invoked directly.

The converter's CLI does not print what its audit repaired: with audit on it
repairs silently, and only exceptions (read, audit or write failures) reach
the per-file .err report. Every .err entry is therefore a finding, but an ODA
"fix count" is not observable.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .report import Finding

ENV_ODAFC = "DWGCHECK_ODAFC"
INSTALL_HINT = (
    "ODA File Converter not found. Install it from "
    "https://www.opendesign.com/guestfiles/oda_file_converter, or set "
    f"{ENV_ODAFC} to the path of the ODAFileConverter executable."
)
CONVERT_TIMEOUT_S = 300
MAX_REPORTED_MESSAGES = 10


class EnvironmentProblem(Exception):
    """Tier 2 cannot run on this machine; maps to exit code 3."""


@dataclass(frozen=True)
class Converter:
    command: tuple[str, ...]


def _candidates(system: str) -> list[Path]:
    if system == "Darwin":
        return [Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")]
    if system == "Windows":
        roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files")]
        found: list[Path] = []
        for root in roots:
            found.extend(sorted(Path(root, "ODA").glob("ODAFileConverter*/ODAFileConverter.exe"), reverse=True))
        return found
    return [Path("/usr/bin/ODAFileConverter"), Path("/usr/local/bin/ODAFileConverter")]


def find_converter() -> Converter:
    system = platform.system()
    configured = os.environ.get(ENV_ODAFC)
    if configured:
        if not Path(configured).is_file():
            raise EnvironmentProblem(f"{ENV_ODAFC}={configured} does not point to a file. {INSTALL_HINT}")
        exe = configured
    else:
        on_path = shutil.which("ODAFileConverter")
        exe = on_path or next((str(p) for p in _candidates(system) if p.is_file()), "")
        if not exe:
            raise EnvironmentProblem(INSTALL_HINT)
    try:
        import ezdxf  # noqa: F401
    except ImportError as exc:
        raise EnvironmentProblem("--deep needs ezdxf: run `uv sync --extra deep`.") from exc

    if system == "Linux" and not os.environ.get("DISPLAY"):
        xvfb = shutil.which("xvfb-run")
        if xvfb is None:
            raise EnvironmentProblem(
                "ODA File Converter needs an X display on Linux. Install xvfb (e.g. `apt install xvfb`) "
                "so it can run under xvfb-run, or set DISPLAY."
            )
        return Converter((xvfb, "-a", exe))
    return Converter((exe,))


ODA = "ODA File Converter"
EZDXF = "ezdxf auditor"


def _finding(source: str, check: str, message: str) -> Finding:
    return Finding(tier=2, check=check, message=message, section=source)


def check_file(path: Path, converter: Converter) -> list[Finding]:
    with tempfile.TemporaryDirectory(prefix="dwgcheck_") as tmp:
        in_dir = Path(tmp, "in")
        out_dir = Path(tmp, "out")
        in_dir.mkdir()
        out_dir.mkdir()
        # The converter works on folders; a private copy guarantees the input
        # is never touched and that only this one file is converted.
        shutil.copyfile(path, in_dir / "input.dwg")
        args = [*converter.command, str(in_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            return [_finding(ODA, "T2.oda.convert", f"did not finish within {CONVERT_TIMEOUT_S}s")]
        except OSError as exc:
            raise EnvironmentProblem(f"could not run ODA File Converter: {exc}") from exc

        copy = in_dir / "input.dwg"
        findings: list[Finding] = []
        for err_file in sorted(out_dir.glob("*.err")):
            text = err_file.read_text(errors="replace")
            for alias in {str(copy), str(copy.resolve())}:
                text = text.replace(alias, str(path))
            findings.append(_finding(ODA, "T2.oda.error", _one_line(text)))
        dxf = out_dir / "input.dxf"
        if not dxf.is_file():
            if not findings:
                detail = _one_line(proc.stderr) or f"exit code {proc.returncode}"
                findings.append(_finding(ODA, "T2.oda.convert", f"no DXF produced ({detail})"))
            return findings
        findings.extend(_audit_dxf(dxf))
        return findings


def _audit_dxf(dxf: Path) -> list[Finding]:
    from ezdxf import recover
    from ezdxf.lldxf.const import DXFStructureError

    try:
        _doc, auditor = recover.readfile(dxf)
    except (DXFStructureError, OSError, UnicodeDecodeError) as exc:
        return [_finding(EZDXF, "T2.ezdxf.load", f"could not load the converted DXF: {exc}")]
    findings: list[Finding] = []
    for kind, entries in (("errors", auditor.errors), ("fixes", auditor.fixes)):
        if not entries:
            continue
        sample = "; ".join(e.message for e in entries[:MAX_REPORTED_MESSAGES])
        more = f" (+{len(entries) - MAX_REPORTED_MESSAGES} more)" if len(entries) > MAX_REPORTED_MESSAGES else ""
        findings.append(_finding(EZDXF, f"T2.ezdxf.{kind}", f"{len(entries)} {kind}: {sample}{more}"))
    return findings


def _one_line(text: str) -> str:
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())
