import threading
from pathlib import Path

from app.config import Settings
from app.downloader import build_ydl_options
from app.tasks import DownloadTask


def test_download_uses_best_source_quality_without_resolution_limit(tmp_path: Path) -> None:
    task = DownloadTask(
        source_type="url",
        source="https://example.test/master.m3u8",
        source_fingerprint="fingerprint",
        output_name="video",
    )

    options = build_ydl_options(task, tmp_path, Settings(), threading.Event())

    assert options["format"] == "bv*+ba/best"
    assert "format_sort" not in options
    assert options["nocheckcertificate"] is False


def test_download_can_ignore_certificate_errors_per_task(tmp_path: Path) -> None:
    task = DownloadTask(
        source_type="url",
        source="https://example.test/master.m3u8",
        source_fingerprint="fingerprint",
        output_name="video",
        ignore_certificate_errors=True,
    )

    options = build_ydl_options(task, tmp_path, Settings(), threading.Event())

    assert options["nocheckcertificate"] is True
