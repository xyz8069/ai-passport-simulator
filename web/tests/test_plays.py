from __future__ import annotations

import hashlib
import struct

import pytest

from app.plays import PlayImportError, PlayCatalogError, PlayCatalogClient, normalize_catalog, resolve_download_url


def make_image() -> bytes:
    payload = b"catalog-fixture"
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    struct.pack_into("<I", header, 4, 0x40380000)
    struct.pack_into("<H", header, 12, 0x0005)
    segment = struct.pack("<II", 0x3FC80000, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image = bytes(header) + segment + bytes([checksum])
    return image + bytes((-len(image)) % 16)


def play_payload(data: bytes, *, url: str = "/api/download/community/catalog-fixture", sha256: str | None = None):
    return {
        "ok": True,
        "plays": [
            {
                "id": 96,
                "projectId": 96,
                "slug": "catalog-fixture",
                "source": "community",
                "isOfficial": False,
                "status": "published",
                "title": {"zh": "目录测试玩法", "en": "Catalog Fixture"},
                "description": {"zh": "来自官方目录的测试项目。", "en": "A catalog fixture."},
                "category": {"zh": "实用工具", "en": "Utilities"},
                "author": "Test Author",
                "firmware": {
                    "available": True,
                    "size": len(data),
                    "sha256": sha256 or hashlib.sha256(data).hexdigest(),
                    "url": url,
                    "format": "esp-merged-0x0",
                },
            }
        ],
    }


class FakeCatalog:
    def __init__(self, data: bytes, *, error: Exception | None = None):
        normalized = normalize_catalog(play_payload(data), 8 * 1024 * 1024)
        self.play = normalized[0]
        self.data = data
        self.error = error

    def catalog(self, max_firmware_bytes: int, force: bool = False):
        return [self.play], 123.0

    def find(self, slug: str, max_firmware_bytes: int):
        if slug != self.play["slug"]:
            raise PlayCatalogError("not found")
        return self.play

    def download_firmware(self, play, max_firmware_bytes: int):
        if self.error:
            raise self.error
        return self.data


def test_catalog_normalization_and_origin_constraint():
    data = make_image()
    play = normalize_catalog(play_payload(data), 8 * 1024 * 1024)[0]
    assert play["title"]["zh"] == "目录测试玩法"
    assert play["firmware"]["available"] is True
    assert resolve_download_url(play["firmware"]["url"]) == (
        "https://ai-passport.folotoy.cn/api/download/community/catalog-fixture"
    )
    with pytest.raises(PlayImportError):
        resolve_download_url("https://example.com/firmware.bin")
    with pytest.raises(PlayImportError):
        resolve_download_url("/api/download/../secret")


def test_catalog_import_is_validated_and_deduplicated(client):
    data = make_image()
    client.application.extensions["play_catalog"] = FakeCatalog(data)

    response = client.get("/api/plays")
    assert response.status_code == 200
    assert response.json["count"] == 1
    assert response.json["plays"][0]["title"]["zh"] == "目录测试玩法"

    response = client.post("/api/plays/catalog-fixture/download")
    assert response.status_code == 201
    assert response.json["imported"] is True
    assert response.json["firmware"]["source"] == {
        "type": "catalog",
        "play_slug": "catalog-fixture",
        "play_title": "目录测试玩法",
        "play_source": "community",
    }
    artifact_id = response.json["firmware"]["id"]

    response = client.post("/api/plays/catalog-fixture/import")
    assert response.status_code == 200
    assert response.json["reused"] is True
    assert response.json["firmware"]["id"] == artifact_id
    assert len(client.get("/api/firmware").json["firmware"]) == 1


def test_catalog_import_rejects_download_errors_without_artifact(client):
    client.application.extensions["play_catalog"] = FakeCatalog(
        make_image(), error=PlayImportError("downloaded firmware SHA-256 does not match the official catalog")
    )
    response = client.post("/api/plays/catalog-fixture/download")
    assert response.status_code == 400
    assert "SHA-256" in response.json["error"]
    assert client.get("/api/firmware").json["firmware"] == []


class FakeResponse:
    def __init__(self, url: str, body: bytes):
        self._url = url
        self._body = body
        self._read = False
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, _size=-1):
        if self._read:
            return b""
        self._read = True
        return self._body


def test_catalog_client_download_checks_declared_hash():
    data = make_image()
    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse(request.full_url, data)

    client = PlayCatalogClient(opener_factory=lambda _handler: FakeOpener())
    play = normalize_catalog(play_payload(data, sha256="0" * 64), 8 * 1024 * 1024)[0]
    with pytest.raises(PlayImportError, match="SHA-256"):
        client.download_firmware(play, 8 * 1024 * 1024)
