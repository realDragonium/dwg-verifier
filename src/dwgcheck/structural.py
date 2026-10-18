"""Tier 1: structural validation of the R2004-style container (ODA spec ch. 4)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import r2004
from .r2004 import DecompressError
from .report import Finding

SUPPORTED_VERSIONS = {"AC1024": "R2010", "AC1027": "R2013", "AC1032": "R2018"}
REQUIRED_SECTIONS = ("AcDb:Header", "AcDb:Classes", "AcDb:Handles", "AcDb:AcDbObjects")
SECTION_DESCRIPTION_SIZE = 0x60
SECTION_PAGE_REF_SIZE = 0x10
SECURITY_FLAG_ENCRYPT_DATA = 0x0001


class _Fatal(Exception):
    """A failure that makes the rest of the container unreadable."""

    def __init__(self, finding: Finding) -> None:
        super().__init__(finding.message)
        self.finding = finding


def _finding(check: str, message: str, offset: int | None = None, section: str | None = None) -> Finding:
    return Finding(tier=1, check=check, message=message, offset=offset, section=section)


@dataclass(frozen=True)
class FileHeader:
    second_header_address: int
    page_map_address: int
    section_map_id: int


@dataclass(frozen=True)
class PageEntry:
    number: int
    size: int
    address: int


@dataclass(frozen=True)
class PageRef:
    number: int
    data_size: int
    start_offset: int


@dataclass(frozen=True)
class SectionInfo:
    name: str
    section_id: int
    max_page_size: int
    compressed: int
    pages: tuple[PageRef, ...]


@dataclass(frozen=True)
class Tier1Result:
    version: str | None
    findings: list[Finding]
    unsupported: str | None = None


def check_bytes(data: bytes) -> Tier1Result:
    if len(data) < r2004.FILE_HEADER_SIZE:
        return Tier1Result(
            data[0:6].decode("ascii", errors="replace") or None,
            [_finding("T1.size", f"file is {len(data)} bytes, smaller than the 0x100-byte file header", 0)],
        )
    version = data[0:6].decode("ascii", errors="replace")
    if version not in SUPPORTED_VERSIONS:
        return Tier1Result(version, [], unsupported=f"version string {version!r} is not AC1024, AC1027 or AC1032")
    # Data pages of password-protected drawings are encrypted with a key we
    # do not have, so their checksums and compression cannot be verified.
    if struct.unpack_from("<I", data, 0x18)[0] & SECURITY_FLAG_ENCRYPT_DATA:
        return Tier1Result(version, [], unsupported="password-encrypted drawing (security flag 0x1 set)")

    findings: list[Finding] = []
    try:
        header = _check_file_header(data, findings)
        pages = _load_page_map(data, header)
        sections = _load_section_map(data, header, pages)
    except _Fatal as fatal:
        findings.append(fatal.finding)
        return Tier1Result(version, findings)

    page_by_number = {p.number: p for p in pages if p.number > 0}
    for section in sections:
        findings.extend(_check_section_pages(data, section, page_by_number))

    present = {s.name for s in sections}
    for name in REQUIRED_SECTIONS:
        if name not in present:
            findings.append(_finding("T1.sections.required", "required section missing from section map", section=name))
    return Tier1Result(version, findings)


def _check_file_header(data: bytes, findings: list[Finding]) -> FileHeader:
    start = r2004.ENCRYPTED_HEADER_OFFSET
    decrypted = r2004.decrypt_file_header(data[start : start + r2004.ENCRYPTED_HEADER_SIZE])
    if decrypted[:12] != r2004.FILE_ID:
        raise _Fatal(_finding("T1.header.id", "decrypted file header lacks the AcFssFcAJMB file ID", start))
    stored_crc = struct.unpack_from("<I", decrypted, 0x68)[0]
    computed = r2004.crc32(decrypted[:0x68] + b"\x00\x00\x00\x00")
    if stored_crc != computed:
        raise _Fatal(
            _finding(
                "T1.header.crc",
                f"file header CRC32 mismatch: stored 0x{stored_crc:08x}, computed 0x{computed:08x}",
                start + 0x68,
            )
        )
    header = FileHeader(
        second_header_address=struct.unpack_from("<Q", decrypted, 0x34)[0],
        # Spec 4.1: the stored address is relative to the end of the 0x100-byte file header.
        page_map_address=struct.unpack_from("<Q", decrypted, 0x54)[0] + r2004.FILE_HEADER_SIZE,
        section_map_id=struct.unpack_from("<I", decrypted, 0x5C)[0],
    )

    # Spec 4.1 says the header data is repeated at the end of the file. In
    # every observed file the copy is the last 0x6C bytes, so anything after
    # it (or a missing/different copy) means the file was truncated, extended
    # or damaged at the tail.
    copy_at = header.second_header_address
    copy_end = copy_at + r2004.ENCRYPTED_HEADER_SIZE
    if copy_end > len(data):
        findings.append(
            _finding(
                "T1.header.copy",
                f"second header copy at 0x{copy_at:x} extends past end of file (0x{len(data):x}); file is truncated",
                copy_at,
            )
        )
    else:
        if data[copy_at:copy_end] != data[start : start + r2004.ENCRYPTED_HEADER_SIZE]:
            findings.append(_finding("T1.header.copy", "second header copy differs from the primary header", copy_at))
        if len(data) > copy_end:
            findings.append(
                _finding(
                    "T1.trailing",
                    f"{len(data) - copy_end} unexpected bytes after the second header copy",
                    copy_end,
                )
            )
    return header


def _read_system_page(data: bytes, address: int, expected_type: int, name: str, check: str) -> bytes:
    header_end = address + r2004.SYSTEM_PAGE_HEADER_SIZE
    if address < r2004.FILE_HEADER_SIZE or header_end > len(data):
        raise _Fatal(_finding(f"{check}.bounds", f"{name} page header at 0x{address:x} lies outside the file", address))
    page_type, decompressed_size, compressed_size, compression, stored = struct.unpack_from("<5I", data, address)
    if page_type != expected_type:
        raise _Fatal(
            _finding(f"{check}.header", f"{name} page type 0x{page_type:08x}, expected 0x{expected_type:08x}", address)
        )
    if compression != 2:
        raise _Fatal(_finding(f"{check}.header", f"{name} compression type {compression}, expected 2", address + 0x0C))
    if header_end + compressed_size > len(data):
        raise _Fatal(
            _finding(
                f"{check}.bounds",
                f"{name} compressed data (0x{compressed_size:x} bytes) extends past end of file",
                address,
            )
        )
    payload = data[header_end : header_end + compressed_size]
    zeroed = struct.pack("<5I", page_type, decompressed_size, compressed_size, compression, 0)
    computed = r2004.page_checksum(payload, r2004.page_checksum(zeroed))
    if computed != stored:
        raise _Fatal(
            _finding(
                f"{check}.checksum",
                f"{name} page checksum mismatch: stored 0x{stored:08x}, computed 0x{computed:08x}",
                address + 0x10,
            )
        )
    try:
        out = r2004.decompress(payload, decompressed_size)
    except DecompressError as exc:
        raise _Fatal(
            _finding(f"{check}.decompress", f"{name} decompression failed: {exc}", header_end + exc.offset)
        ) from exc
    if len(out) != decompressed_size:
        raise _Fatal(
            _finding(
                f"{check}.decompress",
                f"{name} decompressed to 0x{len(out):x} bytes, header declares 0x{decompressed_size:x}",
                address,
            )
        )
    return out


def _load_page_map(data: bytes, header: FileHeader) -> list[PageEntry]:
    raw = _read_system_page(data, header.page_map_address, r2004.PAGE_TYPE_PAGE_MAP, "section page map", "T1.pagemap")
    entries: list[PageEntry] = []
    pos = 0
    address = r2004.FILE_HEADER_SIZE
    while pos < len(raw):
        if pos + 8 > len(raw):
            raise _Fatal(_finding("T1.pagemap.entries", "section page map ends inside an entry", header.page_map_address))
        number, size = struct.unpack_from("<iI", raw, pos)
        pos += 8
        if number < 0:
            pos += 16  # gap entry: parent, left, right, 0
        entries.append(PageEntry(number, size, address))
        address += size

    numbers = [e.number for e in entries if e.number > 0]
    if len(numbers) != len(set(numbers)):
        raise _Fatal(_finding("T1.pagemap.entries", "section page map lists a page number twice", header.page_map_address))
    if not any(e.number > 0 and e.address == header.page_map_address for e in entries):
        raise _Fatal(
            _finding("T1.pagemap.entries", "section page map does not list its own page", header.page_map_address)
        )
    if header.section_map_id not in numbers:
        raise _Fatal(
            _finding(
                "T1.pagemap.entries",
                f"section map page {header.section_map_id} not present in section page map",
                header.page_map_address,
            )
        )
    return entries


def _load_section_map(data: bytes, header: FileHeader, pages: list[PageEntry]) -> list[SectionInfo]:
    entry = next(p for p in pages if p.number == header.section_map_id)
    raw = _read_system_page(data, entry.address, r2004.PAGE_TYPE_SECTION_MAP, "section map", "T1.sectionmap")

    def bad(message: str) -> _Fatal:
        return _Fatal(_finding("T1.sectionmap.entries", message, entry.address))

    if len(raw) < 0x14:
        raise bad("section map shorter than its 0x14-byte header")
    count = struct.unpack_from("<I", raw, 0)[0]
    pos = 0x14
    sections: list[SectionInfo] = []
    seen_pages: set[int] = set()
    for _ in range(count):
        if pos + SECTION_DESCRIPTION_SIZE > len(raw):
            raise bad(f"section map declares {count} sections but ends after {len(sections)}")
        _size, page_count, max_page, _unknown, compressed, section_id, _encrypted = struct.unpack_from("<QIIIIII", raw, pos)
        name = raw[pos + 0x20 : pos + 0x60].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        pos += SECTION_DESCRIPTION_SIZE
        if pos + page_count * SECTION_PAGE_REF_SIZE > len(raw):
            raise bad(f"section {name!r} declares {page_count} pages past the end of the section map")
        refs = []
        for _ in range(page_count):
            number, data_size, start_offset = struct.unpack_from("<IIQ", raw, pos)
            pos += SECTION_PAGE_REF_SIZE
            if number in seen_pages:
                raise bad(f"page {number} is assigned to more than one section")
            seen_pages.add(number)
            refs.append(PageRef(number, data_size, start_offset))
        if compressed not in (1, 2):
            raise bad(f"section {name!r} has compression flag {compressed}, expected 1 or 2")
        if page_count and max_page == 0:
            raise bad(f"section {name!r} has a zero page size")
        sections.append(SectionInfo(name, section_id, max_page, compressed, tuple(refs)))
    if pos != len(raw):
        raise bad(f"{len(raw) - pos} unexplained bytes after the last section description")
    return sections


def _check_section_pages(data: bytes, section: SectionInfo, pages: dict[int, PageEntry]) -> list[Finding]:
    findings: list[Finding] = []
    name = section.name or "<empty section>"
    expected_offset = 0
    for ref in section.pages:
        entry = pages.get(ref.number)
        if entry is None:
            findings.append(_finding("T1.page.missing", f"page {ref.number} not in section page map", section=name))
            continue
        if ref.start_offset < expected_offset or (ref.start_offset - expected_offset) % section.max_page_size:
            findings.append(
                _finding(
                    "T1.page.order",
                    f"page {ref.number} start offset 0x{ref.start_offset:x} inconsistent with preceding pages",
                    entry.address,
                    name,
                )
            )
        # Omitted all-zero pages are legal (spec 4.5); the next present page
        # simply starts further on.
        expected_offset = ref.start_offset + section.max_page_size
        finding = _check_data_page(data, section, ref, entry, name)
        if finding is not None:
            findings.append(finding)
    return findings


def _check_data_page(
    data: bytes, section: SectionInfo, ref: PageRef, entry: PageEntry, name: str
) -> Finding | None:
    address = entry.address
    if entry.size < r2004.DATA_PAGE_HEADER_SIZE:
        return _finding("T1.page.header", f"page {ref.number} size 0x{entry.size:x} is smaller than its header", address, name)
    if address + entry.size > len(data):
        return _finding(
            "T1.page.bounds",
            f"page {ref.number} (0x{entry.size:x} bytes) extends past end of file (0x{len(data):x})",
            address,
            name,
        )
    raw_header = data[address : address + r2004.DATA_PAGE_HEADER_SIZE]
    header = r2004.unmask_data_page_header(raw_header, address)
    page_type, section_number, data_size, page_size, start_offset, header_sum, data_sum = struct.unpack_from(
        "<IIIIQII", header
    )
    mismatches = []
    if page_type != r2004.PAGE_TYPE_DATA:
        mismatches.append(f"type 0x{page_type:08x}")
    if section_number != section.section_id:
        mismatches.append(f"section number {section_number} (expected {section.section_id})")
    if data_size != ref.data_size:
        mismatches.append(f"data size 0x{data_size:x} (section map says 0x{ref.data_size:x})")
    if page_size != entry.size:
        mismatches.append(f"page size 0x{page_size:x} (page map says 0x{entry.size:x})")
    if start_offset != ref.start_offset:
        mismatches.append(f"start offset 0x{start_offset:x} (section map says 0x{ref.start_offset:x})")
    if mismatches:
        return _finding("T1.page.header", f"page {ref.number} header: " + ", ".join(mismatches), address, name)
    if r2004.DATA_PAGE_HEADER_SIZE + data_size > entry.size:
        return _finding("T1.page.header", f"page {ref.number} data does not fit its page extent", address, name)

    payload_at = address + r2004.DATA_PAGE_HEADER_SIZE
    payload = memoryview(data)[payload_at : payload_at + data_size]
    computed_data = r2004.page_checksum(payload)
    if computed_data != data_sum:
        return _finding(
            "T1.page.checksum",
            f"page {ref.number} data checksum mismatch: stored 0x{data_sum:08x}, computed 0x{computed_data:08x}",
            address,
            name,
        )
    zeroed = header[:0x18] + b"\x00\x00\x00\x00" + header[0x1C:]
    computed_header = r2004.page_checksum(zeroed, computed_data)
    if computed_header != header_sum:
        return _finding(
            "T1.page.checksum",
            f"page {ref.number} header checksum mismatch: stored 0x{header_sum:08x}, computed 0x{computed_header:08x}",
            address,
            name,
        )

    if section.compressed == 1:
        if data_size != section.max_page_size:
            return _finding(
                "T1.page.size",
                f"uncompressed page {ref.number} stores 0x{data_size:x} bytes, section page size is "
                f"0x{section.max_page_size:x}",
                address,
                name,
            )
        return None
    try:
        out = r2004.decompress(payload, section.max_page_size)
    except DecompressError as exc:
        return _finding(
            "T1.page.decompress", f"page {ref.number} decompression failed: {exc}", payload_at + exc.offset, name
        )
    # Every compressed page in the observed files expands to the full section
    # page size, including the last one (its tail is zero padding).
    if len(out) != section.max_page_size:
        return _finding(
            "T1.page.size",
            f"page {ref.number} decompressed to 0x{len(out):x} bytes, expected 0x{section.max_page_size:x}",
            address,
            name,
        )
    return None


@dataclass(frozen=True)
class Layout:
    header: FileHeader
    pages: list[PageEntry]
    sections: list[SectionInfo]


def read_layout(data: bytes) -> Layout:
    """Container layout of a file whose file header and maps are intact."""
    try:
        header = _check_file_header(data, [])
        pages = _load_page_map(data, header)
        return Layout(header, pages, _load_section_map(data, header, pages))
    except _Fatal as fatal:
        raise ValueError(fatal.finding.message) from fatal
