from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path

from . import semantic, structural
from .report import FileReport, Status

EXIT_VALID = 0
EXIT_INVALID = 1
EXIT_UNSUPPORTED = 2
EXIT_ENVIRONMENT = 3


def check_path(path: Path, converter: semantic.Converter | None) -> FileReport:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return FileReport(str(path), Status.ERROR, note=f"cannot read file: {exc.strerror or exc}")
    tier1 = structural.check_bytes(data)
    if tier1.unsupported is not None:
        return FileReport(str(path), Status.UNSUPPORTED, version=tier1.version, note=tier1.unsupported)
    report = FileReport(str(path), Status.VALID, version=tier1.version, findings=list(tier1.findings), tiers_run=[1])
    if converter is not None:
        report.findings.extend(semantic.check_file(path, converter))
        report.tiers_run.append(2)
    if report.findings:
        report.status = Status.INVALID
    return report


def collect(target: Path, recursive: bool) -> list[Path]:
    if target.is_file():
        return [target]
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in target.glob(pattern) if p.is_file() and p.suffix.lower() == ".dwg")


def exit_code(reports: Iterable[FileReport]) -> int:
    statuses = {r.status for r in reports}
    if Status.ERROR in statuses:
        return EXIT_ENVIRONMENT
    if Status.INVALID in statuses:
        return EXIT_INVALID
    if Status.UNSUPPORTED in statuses:
        return EXIT_UNSUPPORTED
    return EXIT_VALID


def format_human(report: FileReport) -> str:
    version = f" ({report.version})" if report.version else ""
    if report.status is Status.VALID:
        tiers = "+".join(f"T{t}" for t in report.tiers_run)
        return f"OK       {report.file}{version} [{tiers}]"
    if report.status is not Status.INVALID:
        return f"{report.status.value.upper():<8} {report.file}{version}: {report.note}"
    lines = [f"INVALID  {report.file}{version}: {len(report.findings)} finding(s)"]
    for f in report.findings:
        lines.append(f"  [T{f.tier}] {f.check} @ {f.location()}: {f.message}")
    return "\n".join(lines)


def to_json(reports: Sequence[FileReport], code: int) -> str:
    def encode(r: FileReport) -> dict[str, object]:
        d = asdict(r)
        d["status"] = r.status.value
        d["findings"] = [{"file": r.file, **asdict(f)} for f in r.findings]
        return d

    return json.dumps({"exit_code": code, "files": [encode(r) for r in reports]}, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dwgcheck", description="Report whether DWG files (2010/2013/2018) are structurally intact."
    )
    parser.add_argument("target", type=Path, help="DWG file or directory")
    parser.add_argument("--deep", action="store_true", help="also run Tier 2 (ODA File Converter + ezdxf audit)")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    parser.add_argument("-r", "--recursive", action="store_true", help="descend into subdirectories")
    args = parser.parse_args(argv)

    if not args.target.exists():
        print(f"dwgcheck: {args.target}: no such file or directory", file=sys.stderr)
        return EXIT_ENVIRONMENT
    files = collect(args.target, args.recursive)
    if not files:
        print(f"dwgcheck: no .dwg files in {args.target}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    converter: semantic.Converter | None = None
    if args.deep:
        try:
            converter = semantic.find_converter()
        except semantic.EnvironmentProblem as exc:
            print(f"dwgcheck: {exc}", file=sys.stderr)
            return EXIT_ENVIRONMENT

    reports: list[FileReport] = []
    try:
        for path in files:
            report = check_path(path, converter)
            reports.append(report)
            if not args.json:
                print(format_human(report), flush=True)
    except semantic.EnvironmentProblem as exc:
        print(f"dwgcheck: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    code = exit_code(reports)
    if args.json:
        print(to_json(reports, code))
    return code


if __name__ == "__main__":
    sys.exit(main())
