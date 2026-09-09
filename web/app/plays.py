from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


OFFICIAL_ORIGIN = "https://ai-passport.folotoy.cn"
CATALOG_URL = f"{OFFICIAL_ORIGIN}/api/plays/catalog"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_CACHE_TTL_SECONDS = 60
MAX_CATALOG_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PlayCatalogError(RuntimeError):
    """Raised when the official play catalog cannot be trusted or reached."""


class PlayNotFoundError(PlayCatalogError):
    """Raised when a requested slug is absent from the current catalog."""


class PlayImportError(PlayCatalogError):
    """Raised when a catalog firmware cannot be safely imported."""


class _PinnedRedirectHandler(HTTPRedirectHandler):
    """Allow redirects only while the request remains on the official origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_official_url(newurl):
            raise PlayCatalogError("official server redirected to an untrusted origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_official_url(value: str) -> bool:
    parts = urlsplit(value)
    return (
        parts.scheme == "https"
        and parts.hostname == urlsplit(OFFICIAL_ORIGIN).hostname
        and parts.port in (None, 443)
        and not parts.username
        and not parts.password
    )


def resolve_download_url(value: Any) -> str:
    """Resolve one catalog path without accepting arbitrary remote URLs."""
    if not isinstance(value, str) or not value.startswith("/api/download/"):
        raise PlayImportError("catalog firmware URL must be a relative /api/download/ path")
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise PlayImportError("catalog firmware URL must not contain an external origin or query")
    if "\\" in parts.path or any(part == ".." for part in parts.path.split("/")):
        raise PlayImportError("catalog firmware URL contains an invalid path")
    resolved = urljoin(f"{OFFICIAL_ORIGIN}/", parts.path.lstrip("/"))
    if not is_official_url(resolved):
        raise PlayImportError("catalog firmware URL resolved outside the official origin")
    return resolved


def _localized(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, str):
        return {"zh": value, "en": value}
    if not isinstance(value, dict):
        return {"zh": fallback, "en": fallback}
    zh = value.get("zh") or value.get("en") or fallback
    en = value.get("en") or value.get("zh") or fallback
    return {"zh": str(zh), "en": str(en)}


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _nonnegative_int(value: Any, field: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise PlayCatalogError(f"catalog field {field} must be an integer") from error
    if number < 0:
        raise PlayCatalogError(f"catalog field {field} must not be negative")
    return number


def normalize_play(raw: Any, max_firmware_bytes: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlayCatalogError("catalog item must be an object")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise PlayCatalogError("catalog item has an invalid slug")

    firmware_raw = raw.get("firmware")
    if not isinstance(firmware_raw, dict):
        firmware_raw = {}
    firmware_size = _nonnegative_int(firmware_raw.get("size"), "firmware.size")
    firmware_sha256 = _optional_text(firmware_raw.get("sha256"))
    if firmware_sha256 and not SHA256_RE.fullmatch(firmware_sha256):
        firmware_sha256 = None
    firmware_url = firmware_raw.get("url") or raw.get("downloadUrl")
    if firmware_url is not None:
        # Validate now so a malformed catalog item is visible in the catalog
        # response instead of becoming an unsafe request later.
        resolve_download_url(firmware_url)
    available = bool(firmware_raw.get("available")) and bool(firmware_url)
    if firmware_size is not None and firmware_size > max_firmware_bytes:
        available = False

    title = _localized(raw.get("title"), slug)
    description = _localized(raw.get("description"), raw.get("summary", ""))
    summary = _localized(raw.get("summary"), description["zh"])
    category = _localized(raw.get("category"), "未分类")
    author = raw.get("author")
    if isinstance(author, dict):
        author = author.get("name")

    return {
        "id": raw.get("id"),
        "project_id": raw.get("projectId"),
        "slug": slug,
        "source": _optional_text(raw.get("source")) or "community",
        "is_official": bool(raw.get("isOfficial")),
        "status": _optional_text(raw.get("status")) or "unknown",
        "title": title,
        "description": description,
        "summary": summary,
        "category": category,
        "category_key": _optional_text(raw.get("categoryKey")),
        "author": _optional_text(author),
        "author_profile": raw.get("authorProfile") if isinstance(raw.get("authorProfile"), dict) else {},
        "image": _optional_text(raw.get("image")),
        "github_url": _optional_text(raw.get("githubUrl")),
        "published_at": _optional_text(raw.get("publishedAt")),
        "version": _optional_text(raw.get("version")),
        "views": _nonnegative_int(raw.get("views"), "views") or 0,
        "downloads": _nonnegative_int(raw.get("downloads"), "downloads") or 0,
        "hearts": _nonnegative_int(raw.get("hearts"), "hearts") or 0,
        "firmware": {
            "available": available,
            "size": firmware_size,
            "sha256": firmware_sha256,
            "url": firmware_url if isinstance(firmware_url, str) else None,
            "format": _optional_text(firmware_raw.get("format")),
        },
    }


def normalize_catalog(payload: Any, max_firmware_bytes: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PlayCatalogError("official catalog returned an invalid response")
    items = payload.get("plays")
    if not isinstance(items, list):
        raise PlayCatalogError("official catalog response does not contain a plays list")
    result = [normalize_play(item, max_firmware_bytes) for item in items]
    slugs = [item["slug"] for item in result]
    if len(slugs) != len(set(slugs)):
        raise PlayCatalogError("official catalog contains duplicate slugs")
    return result


@dataclass
class CatalogSnapshot:
    plays: list[dict[str, Any]]
    fetched_at: float
    expires_at: float


class PlayCatalogClient:
    """Fetch and import firmware from the pinned official catalog origin."""

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
        opener_factory: Callable[..., Any] = build_opener,
    ) -> None:
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._opener = opener_factory(_PinnedRedirectHandler())
        self._lock = threading.RLock()
        self._snapshot: Optional[CatalogSnapshot] = None

    def _fetch_bytes(self, url: str, max_bytes: int) -> bytes:
        if not is_official_url(url):
            raise PlayCatalogError("request URL is outside the official origin")
        request = Request(url, headers={"Accept": "application/json, application/octet-stream"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                if not is_official_url(final_url):
                    raise PlayCatalogError("official server returned content from an untrusted origin")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise PlayCatalogError("remote response exceeds the firmware size limit")
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise PlayCatalogError("remote response exceeds the firmware size limit")
                return b"".join(chunks)
        except PlayCatalogError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            raise PlayCatalogError(f"official catalog request failed: {error}") from error

    def _fetch_json(self) -> Any:
        data = self._fetch_bytes(CATALOG_URL, MAX_CATALOG_BYTES)
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlayCatalogError("official catalog is not valid UTF-8 JSON") from error

    def catalog(self, max_firmware_bytes: int, force: bool = False) -> tuple[list[dict[str, Any]], float]:
        now = time.monotonic()
        with self._lock:
            if self._snapshot and not force and now < self._snapshot.expires_at:
                return self._snapshot.plays, self._snapshot.fetched_at
            normalized = normalize_catalog(self._fetch_json(), max_firmware_bytes)
            self._snapshot = CatalogSnapshot(normalized, time.time(), now + self.cache_ttl)
            return normalized, self._snapshot.fetched_at

    def find(self, slug: str, max_firmware_bytes: int) -> dict[str, Any]:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise PlayNotFoundError("invalid play slug")
        plays, _ = self.catalog(max_firmware_bytes)
        for play in plays:
            if play["slug"] == slug:
                return play
        raise PlayNotFoundError("play was not found in the official catalog")

    def download_firmware(self, play: dict[str, Any], max_firmware_bytes: int) -> bytes:
        firmware = play.get("firmware") or {}
        if play.get("status") != "published":
            raise PlayImportError("only published catalog projects can be imported")
        if not firmware.get("available") or not firmware.get("url"):
            raise PlayImportError("this catalog project has no downloadable firmware")
        if not firmware.get("sha256"):
            raise PlayImportError("catalog firmware is missing a valid SHA-256")
        url = resolve_download_url(firmware["url"])
        data = self._fetch_bytes(url, max_firmware_bytes)
        declared_size = firmware.get("size")
        if declared_size is not None and len(data) != declared_size:
            raise PlayImportError(
                f"downloaded firmware size mismatch: expected {declared_size}, got {len(data)}"
            )
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != firmware["sha256"].lower():
            raise PlayImportError("downloaded firmware SHA-256 does not match the official catalog")
        return data
