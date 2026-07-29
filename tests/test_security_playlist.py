from unittest.mock import patch

import pytest

from app.playlist import normalize_uploaded_playlist
from app.security import sanitize_output_name, sanitize_subdir, validate_remote_url


def test_sanitize_paths() -> None:
    assert sanitize_output_name("My: Video.mp4") == "My_ Video"
    assert sanitize_subdir("shows/season 1") == "shows/season 1"
    with pytest.raises(ValueError):
        sanitize_subdir("../outside")
    with pytest.raises(ValueError):
        sanitize_subdir("C:\\Videos")


@patch("app.security.socket.getaddrinfo")
def test_rejects_private_url(mock_getaddrinfo: object) -> None:
    mock_getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Private"):
        validate_remote_url("https://example.test/video.m3u8")


@patch("app.security.socket.getaddrinfo")
def test_normalizes_relative_playlist_uris(mock_getaddrinfo: object) -> None:
    mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]  # type: ignore[attr-defined]
    source = b"""#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="keys/video.key"
#EXTINF:6,
segments/001.ts
#EXT-X-ENDLIST
"""

    result = normalize_uploaded_playlist(source, "https://cdn.example.test/path/master.m3u8")

    assert "https://cdn.example.test/path/keys/video.key" in result
    assert "https://cdn.example.test/path/segments/001.ts" in result


def test_relative_playlist_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        normalize_uploaded_playlist(b"#EXTM3U\n#EXTINF:1,\n001.ts\n", None)
