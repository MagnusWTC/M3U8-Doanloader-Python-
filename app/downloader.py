import json
import re
import shutil
import subprocess
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yt_dlp

from app.config import Settings, get_settings
from app.security import safe_output_path, validate_remote_url
from app.tasks import DownloadTask, TaskStatus, task_store, utc_now


class DownloadCancelled(RuntimeError):
    pass


class TaskLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _write(self, level: str, message: str) -> None:
        message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?<redacted>", message)
        message = re.sub(
            r"(?i)(authorization|cookie)(\s*[:=]\s*)[^\s,;]+", r"\1\2<redacted>", message
        )
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(f"{utc_now().isoformat()} {level} {message}\n")

    def debug(self, message: str) -> None:
        if not message.startswith("[debug] "):
            self._write("INFO", message)

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


class ProgressRecorder:
    def __init__(self, task_id: str, cancel_event: threading.Event) -> None:
        self.task_id = task_id
        self.cancel_event = cancel_event
        self.last_update = 0.0

    def __call__(self, progress: dict[str, Any]) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelled("Download cancelled")
        now = time.monotonic()
        if progress.get("status") == "downloading" and now - self.last_update < 1:
            return
        self.last_update = now
        downloaded = int(progress.get("downloaded_bytes") or 0)
        total_value = progress.get("total_bytes") or progress.get("total_bytes_estimate")
        total = int(total_value) if total_value else None
        percent = min(94.0, downloaded / total * 94.0) if total else 0.0
        task = task_store.get(self.task_id)
        if not task:
            return
        task_store.update(
            self.task_id,
            status=TaskStatus.DOWNLOADING.value,
            downloaded_bytes=downloaded,
            total_bytes=total,
            progress=max(task.progress, percent),
            speed=float(progress["speed"]) if progress.get("speed") else None,
            eta=int(progress["eta"]) if progress.get("eta") is not None else None,
        )


def build_ydl_options(
    task: DownloadTask,
    work_dir: Path,
    settings: Settings,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    return {
        "outtmpl": str(work_dir / "media.%(ext)s"),
        "format": "bv*+ba/best",
        "http_headers": task.headers,
        "nocheckcertificate": task.ignore_certificate_errors,
        "socket_timeout": settings.socket_timeout,
        "retries": 5,
        "fragment_retries": settings.fragment_retries,
        "file_access_retries": 3,
        "retry_sleep_functions": {
            "http": lambda attempt: min(20, 2 ** max(0, attempt - 1)),
            "fragment": lambda attempt: min(20, 2 ** max(0, attempt - 1)),
        },
        "concurrent_fragment_downloads": settings.concurrent_fragments,
        "continuedl": True,
        "nopart": False,
        "skip_unavailable_fragments": False,
        "keep_fragments": False,
        "noplaylist": True,
        "cachedir": False,
        "quiet": True,
        "noprogress": True,
        "progress_hooks": [ProgressRecorder(task.id, cancel_event)],
        "logger": TaskLogger(settings.log_root / f"{task.id}.log"),
        "fixup": "detect_or_warn",
    }


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return


def _serve_uploaded_playlist(work_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(_QuietHandler, directory=str(work_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/upload.m3u8"


def _find_downloaded_media(work_dir: Path) -> Path:
    ignored_suffixes = {".part", ".ytdl", ".json", ".m3u8"}
    candidates = [
        path
        for path in work_dir.glob("media.*")
        if path.is_file() and path.suffix.lower() not in ignored_suffixes
    ]
    if not candidates:
        raise RuntimeError("yt-dlp completed without producing a media file")
    return max(candidates, key=lambda path: path.stat().st_size)


def _run_ffmpeg(command: list[str], logger: TaskLogger) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.stderr:
        logger.info(result.stderr[-8000:])
    return result


def finalize_mp4(source: Path, target: Path, logger: TaskLogger) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial_target = target.with_suffix(".mp4.partial")
    partial_target.unlink(missing_ok=True)
    copy_command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(partial_target),
    ]
    result = _run_ffmpeg(copy_command, logger)
    if result.returncode != 0:
        logger.warning("Stream-copy remux failed; falling back to H.264/AAC transcoding")
        partial_target.unlink(missing_ok=True)
        transcode_command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(partial_target),
        ]
        result = _run_ffmpeg(transcode_command, logger)
    if result.returncode != 0 or not partial_target.exists() or partial_target.stat().st_size == 0:
        partial_target.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg could not create a valid MP4 file")
    partial_target.replace(target)


def probe_output(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration,size:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe could not read the generated MP4")
    info: dict[str, Any] = json.loads(result.stdout)
    streams = info.get("streams") or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise RuntimeError("The generated MP4 has no video stream")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("The generated MP4 has an invalid duration")
    return info


def _set_failure(task_id: str, code: str, message: str) -> None:
    task_store.update(
        task_id,
        status=TaskStatus.FAILED.value,
        error_code=code,
        error_message=message[:2000],
    )


def run_download_task(task_id: str, cancel_event: threading.Event) -> None:
    settings = get_settings()
    settings.ensure_directories()
    work_dir = settings.work_root / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    logger = TaskLogger(settings.log_root / f"{task_id}.log")

    task = task_store.get(task_id)
    if not task:
        raise RuntimeError(f"Unknown task: {task_id}")
    task_store.update(
        task_id,
        started_at=task.started_at or utc_now(),
        error_code=None,
        error_message=None,
    )

    delays = [0, 30, 120]
    for attempt in range(1, settings.task_max_attempts + 1):
        server: ThreadingHTTPServer | None = None
        try:
            if cancel_event.is_set():
                raise DownloadCancelled("Download cancelled")
            task = task_store.get(task_id)
            if not task:
                return
            task_store.update(task_id, attempt=attempt, status=TaskStatus.PREPARING.value)
            source_url = task.source
            if task.source_type == "url":
                source_url = validate_remote_url(source_url)
            else:
                server, source_url = _serve_uploaded_playlist(work_dir)
            options = build_ydl_options(task, work_dir, settings, cancel_event)

            with yt_dlp.YoutubeDL(options) as ydl:
                error_code = ydl.download([source_url])
            if error_code:
                raise RuntimeError(f"yt-dlp exited with code {error_code}")

            if cancel_event.is_set():
                raise DownloadCancelled("Download cancelled")
            task = task_store.get(task_id)
            if not task:
                return
            task_store.update(task_id, status=TaskStatus.POSTPROCESSING.value, progress=95.0)
            target = safe_output_path(
                settings.download_root,
                task.output_subdir,
                f"{task.output_name}-{task.id[:8]}.mp4",
            )

            source = _find_downloaded_media(work_dir)
            finalize_mp4(source, target, logger)
            if cancel_event.is_set():
                target.unlink(missing_ok=True)
                raise DownloadCancelled("Download cancelled")
            media_info = probe_output(target)
            task_store.update(
                task_id,
                status=TaskStatus.COMPLETED.value,
                progress=100.0,
                output_path=str(target.relative_to(settings.download_root.resolve())),
                media_info=media_info,
                completed_at=utc_now(),
            )
            for path in work_dir.iterdir():
                if path.name != "upload.m3u8":
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            return
        except Exception as exc:
            logger.error(str(exc))
            if cancel_event.is_set() or isinstance(exc, DownloadCancelled):
                task_store.update(
                    task_id,
                    status=TaskStatus.CANCELLED.value,
                    error_code=None,
                    error_message=None,
                )
                return
            if attempt >= settings.task_max_attempts:
                _set_failure(task_id, "DOWNLOAD_FAILED", str(exc))
                return
            task_store.update(
                task_id,
                status=TaskStatus.RETRY_WAIT.value,
                error_message=str(exc)[:2000],
            )
            if cancel_event.wait(delays[min(attempt, len(delays) - 1)]):
                task_store.update(task_id, status=TaskStatus.CANCELLED.value)
                return
        finally:
            if server:
                server.shutdown()
                server.server_close()
