from __future__ import annotations

import pytest

from dwgcheck import r2004


def test_magic_sequence_matches_spec_table() -> None:
    # First row of the table in spec 4.1.
    assert r2004.magic_sequence(16) == bytes.fromhex("2923BE84E16CD6AE529049F1F1BBE9EB")


def test_crc32_is_standard_crc32() -> None:
    assert r2004.crc32(b"123456789") == 0xCBF43926


def test_page_checksum_matches_reference_definition() -> None:
    data = bytes(range(256)) * 40  # spans more than one 0x15B0 chunk
    sum1, sum2 = 0x1234, 0x5678
    for start in range(0, len(data), 0x15B0):
        for b in data[start : start + 0x15B0]:
            sum1 += b
            sum2 += sum1
        sum1 %= 0xFFF1
        sum2 %= 0xFFF1
    assert r2004.page_checksum(data, 0x56781234) == (sum2 << 16) | sum1


def test_data_page_mask_round_trips() -> None:
    header = bytes(range(32))
    masked = r2004.unmask_data_page_header(header, 0x1234)
    assert masked != header
    assert r2004.unmask_data_page_header(masked, 0x1234) == header


def test_literal_length_examples_from_spec() -> None:
    # 0x05 -> 8 literals; 0x00 0x02 -> 0x14 literals.
    assert r2004.decompress(b"\x05" + b"abcdefgh" + b"\x11", 8) == b"abcdefgh"
    payload = bytes(range(0x14))
    assert r2004.decompress(b"\x00\x02" + payload + b"\x11", 0x14) == payload


def test_overlapping_back_reference_repeats_the_window() -> None:
    # 4 literals "abcd", then opcode 0x60: copy (6-1)=5 bytes from distance
    # ((0x00 << 2) | 3) + 1 = 4, litCount from the low bits = 0 -> literal length
    # byte with high nibble set means none follow and is the next opcode (0x11).
    stream = b"\x01abcd" + bytes([0x6C, 0x00]) + b"\x11"
    assert r2004.decompress(stream, 9) == b"abcdabcda"


@pytest.mark.parametrize(
    ("stream", "size", "reason"),
    [
        (b"\x01abcd", 4, "without terminator"),
        (b"\x01ab", 4, "exceeds compressed data"),
        (bytes([0x01]) + b"abcd" + bytes([0x6C, 0x10]) + b"\x11", 100, "before start"),
        (b"\x05abcdefgh\x11", 4, "exceeds declared"),
        (b"\x01abcd\x11", 1 << 30, "impossible"),
        (b"\x01abcd\x05\x11", 4, "invalid opcode"),
    ],
)
def test_malformed_streams_are_rejected(stream: bytes, size: int, reason: str) -> None:
    with pytest.raises(r2004.DecompressError, match=reason):
        r2004.decompress(stream, size)
