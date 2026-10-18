"""Primitives of the R2004-style DWG container (ODA spec chapter 4).

Shared by AC1018, AC1024, AC1027 and AC1032 (spec chapters 6-8 state the
container is unchanged after R2004).
"""

from __future__ import annotations

import struct
import zlib

FILE_HEADER_SIZE = 0x100
ENCRYPTED_HEADER_OFFSET = 0x80
ENCRYPTED_HEADER_SIZE = 0x6C
FILE_ID = b"AcFssFcAJMB\x00"

PAGE_TYPE_PAGE_MAP = 0x41630E3B
PAGE_TYPE_SECTION_MAP = 0x4163003B
PAGE_TYPE_DATA = 0x4163043B
SYSTEM_PAGE_HEADER_SIZE = 0x14
DATA_PAGE_HEADER_SIZE = 0x20
DATA_PAGE_MASK = 0x4164536B
PAGE_ALIGNMENT = 0x20


class DecompressError(Exception):
    def __init__(self, message: str, offset: int) -> None:
        super().__init__(message)
        self.offset = offset


def magic_sequence(length: int) -> bytes:
    """The LCG byte sequence of spec 4.1, used for the header XOR and padding."""
    out = bytearray(length)
    seed = 1
    for i in range(length):
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        out[i] = (seed >> 16) & 0xFF
    return bytes(out)


_HEADER_MASK = magic_sequence(ENCRYPTED_HEADER_SIZE)


def decrypt_file_header(raw: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(raw, _HEADER_MASK, strict=True))


def crc32(data: bytes, seed: int = 0) -> int:
    # Spec 2.14.2 is the standard reflected CRC-32 (table 0xEDB88320 with
    # pre/post inversion); zlib computes the same function.
    return zlib.crc32(data, seed) & 0xFFFFFFFF


def page_checksum(data: bytes | memoryview, seed: int = 0) -> int:
    """Spec 4.2 section page checksum (Adler-like, 0x15B0-byte chunks)."""
    sum1 = seed & 0xFFFF
    sum2 = seed >> 16
    view = memoryview(data)
    for start in range(0, len(view), 0x15B0):
        for byte in view[start : start + 0x15B0]:
            sum1 += byte
            sum2 += sum1
        sum1 %= 0xFFF1
        sum2 %= 0xFFF1
    return (sum2 << 16) | (sum1 & 0xFFFF)


def unmask_data_page_header(raw: bytes, address: int) -> bytes:
    mask = DATA_PAGE_MASK ^ (address & 0xFFFFFFFF)
    words = struct.unpack("<8I", raw)
    return struct.pack("<8I", *(w ^ mask for w in words))


def decompress(src: bytes | memoryview, expected_size: int) -> bytes:
    """Spec 4.7 LZ77 variant. Raises DecompressError on any malformed input.

    Never produces more than expected_size bytes, so corrupt length fields
    cannot make it allocate unbounded memory.
    """
    data = memoryview(src)
    end = len(data)
    out = bytearray()
    pos = 0

    def read() -> int:
        nonlocal pos
        if pos >= end:
            raise DecompressError("compressed stream ended without terminator", pos)
        b = data[pos]
        pos += 1
        return b

    def literal_length() -> tuple[int, int | None]:
        """Returns (length, opcode) where opcode is set if no literal run follows."""
        b = read()
        if b & 0xF0:
            return 0, b
        if b == 0:
            total = 0x0F
            while (b := read()) == 0:
                total += 0xFF
            return total + b + 3, None
        # Spec 4.7 lists "0x01-0x0E : 4-0x12", which is internally inconsistent
        # (0x0E + 3 = 0x11). Real files use 0x0F as well: every value 1-15 is +3.
        return b + 3, None

    def long_compression_offset() -> int:
        b = read()
        if b != 0:
            return b
        total = 0xFF
        while (b := read()) == 0:
            total += 0xFF
        return total + b

    def two_byte_offset() -> tuple[int, int]:
        first = read()
        offset = (first >> 2) | (read() << 6)
        return offset, first & 0x03

    def copy_literal(count: int) -> None:
        nonlocal pos
        if pos + count > end:
            raise DecompressError("literal run exceeds compressed data", pos)
        if len(out) + count > expected_size:
            raise DecompressError("output exceeds declared decompressed size", pos)
        out.extend(data[pos : pos + count])
        pos += count

    lit, opcode = literal_length()
    copy_literal(lit)
    while True:
        op_pos = pos
        op1 = opcode if opcode is not None else read()
        opcode = None
        if op1 == 0x11:
            break
        if op1 < 0x10:
            raise DecompressError(f"invalid opcode 0x{op1:02x}", op_pos)
        if op1 >= 0x40:
            comp_bytes = (op1 >> 4) - 1
            comp_offset = (read() << 2) | ((op1 & 0x0C) >> 2)
            lit = op1 & 0x03
        else:
            if op1 == 0x10:
                comp_bytes = long_compression_offset() + 9
            elif op1 < 0x20:
                comp_bytes = (op1 & 0x0F) + 2
            elif op1 == 0x20:
                comp_bytes = long_compression_offset() + 0x21
            else:
                comp_bytes = op1 - 0x1E
            comp_offset, lit = two_byte_offset()
            if op1 < 0x20:
                comp_offset += 0x3FFF
        if lit == 0:
            lit, opcode = literal_length()

        # Spec 4.7 gives compOffset as the backwards distance, but real files
        # are off by one: the copy source is compOffset + 1 bytes back.
        distance = comp_offset + 1
        if distance > len(out):
            raise DecompressError("back-reference before start of output", op_pos)
        if len(out) + comp_bytes > expected_size:
            raise DecompressError("output exceeds declared decompressed size", op_pos)
        start = len(out) - distance
        if comp_bytes <= distance:
            out.extend(out[start : start + comp_bytes])
        else:
            for i in range(comp_bytes):
                out.append(out[start + i])
        copy_literal(lit)
    return bytes(out)
