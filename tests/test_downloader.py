import threading
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from app.config import Settings
from app.downloader import TaskLogger, build_ydl_options, cleanup_completed_work_dir, finalize_mp4
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


def test_completed_task_cleanup_removes_entire_work_directory(tmp_path: Path) -> None:
    work_dir = tmp_path / "data" / "work" / "task-id"
    work_dir.mkdir(parents=True)
    (work_dir / "upload.m3u8").write_text("#EXTM3U", encoding="utf-8")
    (work_dir / "media.ts").write_bytes(b"media")
    fragment_dir = work_dir / "media.fragments"
    fragment_dir.mkdir()
    (fragment_dir / "fragment.part").write_bytes(b"fragment")

    cleanup_completed_work_dir(work_dir)

    assert not work_dir.exists()


def test_ffmpeg_failure_removes_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "media.ts"
    target = tmp_path / "video.mp4"
    partial_target = tmp_path / "video.mp4.partial"
    source.write_bytes(b"media")
    partial_target.write_bytes(b"stale")
    failed = CompletedProcess(args=["ffmpeg"], returncode=1, stdout="", stderr="failed")

    with (
        patch("app.downloader._run_ffmpeg", return_value=failed),
        pytest.raises(RuntimeError, match="FFmpeg could not create"),
    ):
        finalize_mp4(source, target, TaskLogger(tmp_path / "task.log"))

    assert not partial_target.exists()
    assert not target.exists()
