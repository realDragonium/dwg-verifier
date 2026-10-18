"""Derive corrupted DWG variants from a valid file.

Every generator is a pure function of (valid bytes, seed), so test runs are
reproducible. Run as a script to write the variants to a directory:

    uv run python -m tools.corruptions fixtures/libredwg/example_2018.dwg out/
"""

from __future__ import annotations

import random
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dwgcheck import r2004
from dwgcheck.structural import Layout, PageEntry, read_layout

Generator = Callable[[bytes, Layout, random.Random], bytes]


@dataclass(frozen=True)
class CorruptionClass:
    name: str
    description: str
    generate: Generator
    variants: int
    # Classes Tier 1 cannot see by construction; they exist to exercise Tier 2.
    tier1_invisible: bool = False


def _truncate(fraction: float) -> Generator:
    def generate(data: bytes, _layout: Layout, _rng: random.Random) -> bytes:
        return data[: int(len(data) * fraction)]

    return generate


def _flip_bit(data: bytes, start: int, end: int, rng: random.Random) -> bytes:
    out = bytearray(data)
    pos = rng.randrange(start, end)
    out[pos] ^= 1 << rng.randrange(8)
    return bytes(out)


def _system_page_extent(data: bytes, address: int) -> tuple[int, int]:
    compressed_size = struct.unpack_from("<I", data, address + 0x08)[0]
    return address, address + r2004.SYSTEM_PAGE_HEADER_SIZE + compressed_size


def _data_pages(layout: Layout) -> list[PageEntry]:
    by_number = {p.number: p for p in layout.pages}
    return [by_number[ref.number] for s in layout.sections for ref in s.pages]


def _data_page_extent(data: bytes, page: PageEntry) -> tuple[int, int]:
    header = r2004.unmask_data_page_header(data[page.address : page.address + 0x20], page.address)
    data_size = struct.unpack_from("<I", header, 0x08)[0]
    return page.address, page.address + r2004.DATA_PAGE_HEADER_SIZE + data_size


def flip_header(data: bytes, _layout: Layout, rng: random.Random) -> bytes:
    start = r2004.ENCRYPTED_HEADER_OFFSET
    return _flip_bit(data, start, start + r2004.ENCRYPTED_HEADER_SIZE, rng)


def flip_page_map(data: bytes, layout: Layout, rng: random.Random) -> bytes:
    return _flip_bit(data, *_system_page_extent(data, layout.header.page_map_address), rng)


def flip_section_map(data: bytes, layout: Layout, rng: random.Random) -> bytes:
    page = next(p for p in layout.pages if p.number == layout.header.section_map_id)
    return _flip_bit(data, *_system_page_extent(data, page.address), rng)


def flip_data_page(data: bytes, layout: Layout, rng: random.Random) -> bytes:
    page = rng.choice(_data_pages(layout))
    return _flip_bit(data, *_data_page_extent(data, page), rng)


def zero_data_page(data: bytes, layout: Layout, rng: random.Random) -> bytes:
    page = rng.choice(_data_pages(layout))
    out = bytearray(data)
    out[page.address : page.address + page.size] = bytes(page.size)
    return bytes(out)


def append_garbage(data: bytes, _layout: Layout, rng: random.Random) -> bytes:
    return data + rng.randbytes(rng.randint(1, 4096))


def semantic_objects(data: bytes, layout: Layout, rng: random.Random) -> bytes:
    """Change object data inside AcDb:AcDbObjects and re-seal every checksum.

    The result is structurally perfect, so only Tier 2 can notice it.
    """
    section = next(s for s in layout.sections if s.name == "AcDb:AcDbObjects")
    by_number = {p.number: p for p in layout.pages}
    page = by_number[rng.choice(section.pages).number]
    start, end = _data_page_extent(data, page)
    payload_at = start + r2004.DATA_PAGE_HEADER_SIZE
    original = r2004.decompress(data[payload_at:end], section.max_page_size)
    out = bytearray(data)
    for _ in range(1000):
        candidate = bytearray(data[payload_at:end])
        candidate[rng.randrange(len(candidate))] ^= 1 << rng.randrange(8)
        try:
            decoded = r2004.decompress(bytes(candidate), section.max_page_size)
        except r2004.DecompressError:
            continue
        if len(decoded) == section.max_page_size and decoded != original:
            break
    else:
        raise RuntimeError("no checksum-preserving edit found")
    out[payload_at:end] = candidate
    header = bytearray(r2004.unmask_data_page_header(data[start:payload_at], start))
    data_sum = r2004.page_checksum(bytes(candidate))
    struct.pack_into("<I", header, 0x1C, data_sum)
    struct.pack_into("<I", header, 0x18, 0)
    struct.pack_into("<I", header, 0x18, r2004.page_checksum(bytes(header), data_sum))
    # The header mask is a plain XOR, so unmasking a decoded header re-masks it.
    out[start:payload_at] = r2004.unmask_data_page_header(bytes(header), start)
    return bytes(out)


CLASSES: tuple[CorruptionClass, ...] = (
    CorruptionClass("truncate_10", "truncated to 10% of size", _truncate(0.10), 1),
    CorruptionClass("truncate_50", "truncated to 50% of size", _truncate(0.50), 1),
    CorruptionClass("truncate_90", "truncated to 90% of size", _truncate(0.90), 1),
    CorruptionClass("truncate_99", "truncated to 99% of size", _truncate(0.99), 1),
    CorruptionClass("flip_header", "1 random bit flipped in the encrypted file header", flip_header, 5),
    CorruptionClass("flip_page_map", "1 random bit flipped in the section page map page", flip_page_map, 5),
    CorruptionClass("flip_section_map", "1 random bit flipped in the section map page", flip_section_map, 5),
    CorruptionClass("flip_data_page", "1 random bit flipped in a random data page", flip_data_page, 5),
    CorruptionClass("zero_data_page", "a whole random data page zeroed", zero_data_page, 3),
    CorruptionClass("append_garbage", "1-4096 random bytes appended after EOF", append_garbage, 3),
    CorruptionClass(
        "semantic_objects",
        "object data altered with all checksums re-sealed",
        semantic_objects,
        3,
        tier1_invisible=True,
    ),
)


@dataclass(frozen=True)
class Variant:
    corruption: CorruptionClass
    index: int
    data: bytes


def variants(data: bytes, seed_name: str, classes: tuple[CorruptionClass, ...] = CLASSES) -> list[Variant]:
    layout = read_layout(data)
    result = []
    for cls in classes:
        for i in range(cls.variants):
            rng = random.Random(f"{seed_name}:{cls.name}:{i}")
            corrupted = cls.generate(data, layout, rng)
            assert corrupted != data, f"{cls.name} produced an unchanged file"
            result.append(Variant(cls, i, corrupted))
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tools.corruptions <valid.dwg> <out-dir>", file=sys.stderr)
        return 2
    source, out_dir = Path(argv[0]), Path(argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for v in variants(source.read_bytes(), source.name):
        (out_dir / f"{source.stem}.{v.corruption.name}.{v.index}.dwg").write_bytes(v.data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
