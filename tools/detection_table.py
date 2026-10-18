"""Detection rate per corruption class per tier, as a Markdown table.

    uv run python -m tools.detection_table            # Tier 1 only
    uv run python -m tools.detection_table --deep     # Tier 1 and Tier 2
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from dwgcheck import semantic
from dwgcheck.structural import check_bytes
from tests.conftest import valid_fixtures
from tools.corruptions import CLASSES, variants


@dataclass
class Tally:
    total: int = 0
    tier1: int = 0
    tier2: int = 0
    either: int = 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args(argv)
    converter = semantic.find_converter() if args.deep else None
    fixtures = valid_fixtures()
    tallies: dict[str, Tally] = defaultdict(Tally)

    with tempfile.TemporaryDirectory(prefix="dwgcheck_table_") as tmp:
        jobs: list[tuple[str, Path, bool]] = []
        for n, fixture in enumerate(fixtures):
            for v in variants(fixture.read_bytes(), fixture.name):
                path = Path(tmp, f"{n}_{v.corruption.name}_{v.index}.dwg")
                path.write_bytes(v.data)
                jobs.append((v.corruption.name, path, bool(check_bytes(v.data).findings)))

        def tier2(path: Path) -> bool:
            return converter is not None and bool(semantic.check_file(path, converter))

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            tier2_hits = list(pool.map(tier2, (p for _, p, _ in jobs)))

    for (name, _, hit1), hit2 in zip(jobs, tier2_hits, strict=True):
        t = tallies[name]
        t.total += 1
        t.tier1 += hit1
        t.tier2 += hit2
        t.either += hit1 or hit2

    def rate(hits: int, total: int) -> str:
        return f"{hits}/{total} ({100 * hits / total:.0f}%)"

    print(f"{len(fixtures)} valid fixtures; each row aggregates every variant derived from them.\n")
    print("| Corruption | Variants | Tier 1 | Tier 2 | Either |")
    print("|---|---|---|---|---|")
    for cls in CLASSES:
        t = tallies[cls.name]
        t2 = rate(t.tier2, t.total) if converter else "not run"
        print(f"| `{cls.name}`: {cls.description} | {t.total} | {rate(t.tier1, t.total)} | {t2} | {rate(t.either, t.total)} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
