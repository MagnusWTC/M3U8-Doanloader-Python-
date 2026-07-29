from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SourceType(str, Enum):
    URL = "url"
    UPLOAD = "upload"


@dataclass
class DownloadTask:
    source_type: str
    source: str
    source_fingerprint: str
    output_name: str
    output_subdir: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    ignore_certificate_errors: bool = False
    upload_path: str | None = None
    base_url: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = TaskStatus.QUEUED.value
    output_path: str | None = None
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    attempt: int = 0
    error_code: str | None = None
    error_message: str | None = None
    media_info: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, DownloadTask] = {}
        self._lock = RLock()

    def add(self, task: DownloadTask) -> DownloadTask:
        with self._lock:
            self._tasks[task.id] = task
            return task

    def get(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **changes: object) -> DownloadTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for name, value in changes.items():
                if not hasattr(task, name):
                    raise AttributeError(f"Unknown task field: {name}")
                setattr(task, name, value)
            task.updated_at = utc_now()
            return task

    def find_by_fingerprint(self, fingerprint: str, statuses: set[str]) -> DownloadTask | None:
        with self._lock:
            matches = [
                task
                for task in self._tasks.values()
                if task.source_fingerprint == fingerprint and task.status in statuses
            ]
            return max(matches, key=lambda task: task.created_at, default=None)

    def paginate(self, page: int, page_size: int) -> tuple[list[DownloadTask], int]:
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda task: task.created_at, reverse=True)
            start = (page - 1) * page_size
            return ordered[start : start + page_size], len(ordered)

    def queued(self, limit: int) -> list[DownloadTask]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda task: task.created_at)
            return [task for task in tasks if task.status == TaskStatus.QUEUED.value][:limit]

    def delete(self, task_id: str) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()


task_store = InMemoryTaskStore()
