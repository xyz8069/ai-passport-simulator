from __future__ import annotations

import struct

import pytest

from app.firmware import ESP32C3_CHIP_ID, FirmwareFormatError, analyze_firmware, parse_image


def make_image(chip_id: int = ESP32C3_CHIP_ID, payload: bytes = b"hello") -> bytes:
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    struct.pack_into("<I", header, 4, 0x40380000)
    struct.pack_into("<H", header, 12, chip_id)
    segment = struct.pack("<II", 0x3FC80000, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image = bytes(header) + segment + bytes([checksum])
    return image + bytes((-len(image)) % 16)


def make_merged() -> bytes:
    partition_table = bytearray(0x1000)

    def entry(index: int, part_type: int, subtype: int, offset: int, size: int, name: str):
        start = index * 32
        struct.pack_into("<HBBII", partition_table, start, 0x50AA, part_type, subtype, offset, size)
        partition_table[start + 12:start + 28] = name.encode()[:15].ljust(16, b"\0")

    entry(0, 1, 2, 0x9000, 0x6000, "nvs")
    entry(1, 0, 0, 0x10000, 0x300000, "factory")
    result = bytearray(0x10000 + len(make_image()) + 0x100)
    result[0x0:0x0 + len(make_image())] = make_image()
    result[0x8000:0x8000 + len(partition_table)] = partition_table
    result[0x10000:0x10000 + len(make_image())] = make_image()
    return bytes(result)


def test_parse_app_image():
    image = parse_image(make_image())
    assert image.chip_id == ESP32C3_CHIP_ID
    assert image.checksum_valid is True
    assert image.segment_count == 1


def test_analyze_merged_image():
    report = analyze_firmware(make_merged(), "merged.bin")
    assert report["format"] == "esp-idf-merged"
    assert report["chip"] == "ESP32-C3"
    assert report["bootable_flash"] is True
    assert any(part["name"] == "factory" for part in report["partitions"])
    assert report["factory_image"]["offset"] == 0x10000


def test_merged_image_without_bootloader_at_zero_is_not_bootable():
    image = bytearray(make_merged())
    image[0:len(make_image())] = b"\xff" * len(make_image())
    image[0x1000:0x1000 + len(make_image())] = make_image()

    report = analyze_firmware(bytes(image), "legacy-layout.bin")

    assert report["format"] == "esp-idf-merged"
    assert report["bootable_flash"] is False


def test_reject_checksum_and_wrong_chip():
    broken = bytearray(make_image())
    broken[32] ^= 1
    with pytest.raises(FirmwareFormatError, match="checksum"):
        analyze_firmware(bytes(broken))
    with pytest.raises(FirmwareFormatError, match="ESP32-C3"):
        analyze_firmware(make_image(chip_id=0x0009))
