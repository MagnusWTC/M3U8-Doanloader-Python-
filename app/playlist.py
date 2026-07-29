from collections.abc import Iterator
from typing import cast
from urllib.parse import urljoin, urlsplit

import m3u8

from app.security import validate_remote_url


def _remote_uri(uri: str, base_url: str | None) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme:
        return validate_remote_url(uri)
    if not base_url:
        raise ValueError("A base_url is required when the playlist contains relative URIs")
    return validate_remote_url(urljoin(base_url, uri))


def _playlist_uris(manifest: m3u8.M3U8) -> Iterator[tuple[object, str]]:
    for playlist in manifest.playlists:
        if playlist.uri:
            yield playlist, "uri"
    for media in manifest.media:
        if media.uri:
            yield media, "uri"
    for segment in manifest.segments:
        if segment.uri:
            yield segment, "uri"
        if segment.key and segment.key.uri:
            yield segment.key, "uri"
        if segment.init_section and segment.init_section.uri:
            yield segment.init_section, "uri"
    for key in manifest.keys:
        if key and key.uri:
            yield key, "uri"


def normalize_uploaded_playlist(content: bytes, base_url: str | None) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The playlist must be UTF-8 encoded") from exc
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("The uploaded file is not an M3U8 playlist")

    normalized_base = validate_remote_url(base_url) if base_url else None
    try:
        manifest = m3u8.loads(text)
    except Exception as exc:
        raise ValueError("The M3U8 playlist could not be parsed") from exc

    seen: set[tuple[int, str]] = set()
    for owner, attribute in _playlist_uris(manifest):
        marker = (id(owner), attribute)
        if marker in seen:
            continue
        seen.add(marker)
        uri = getattr(owner, attribute)
        setattr(owner, attribute, _remote_uri(uri, normalized_base))
    return cast(str, manifest.dumps())
