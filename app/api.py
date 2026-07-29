import hashlib
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import get_settings
from app.playlist import normalize_uploaded_playlist
from app.schemas import DeleteResponse, TaskListResponse, TaskResponse, UrlTaskCreate
from app.security import (
    infer_referer,
    safe_output_path,
    sanitize_output_name,
    sanitize_subdir,
    validate_headers,
    validate_remote_url,
)
from app.tasks import DownloadTask, SourceType, TaskStatus, task_store

router = APIRouter(prefix="/api/v1")
settings = get_settings()


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _fingerprint(*values: object) -> str:
    serialized = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _existing_task(fingerprint: str) -> DownloadTask | None:
    return task_store.find_by_fingerprint(
        fingerprint,
        {
            TaskStatus.QUEUED.value,
            TaskStatus.PREPARING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.POSTPROCESSING.value,
            TaskStatus.RETRY_WAIT.value,
            TaskStatus.COMPLETED.value,
        },
    )


@router.get("/referer-preview")
def referer_preview(url: str = Query(...)) -> dict[str, str]:
    try:
        return {"referer": infer_referer(url)}
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc


@router.post("/tasks/url", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_url_task(payload: UrlTaskCreate) -> DownloadTask:
    try:
        url = validate_remote_url(payload.url)
        output_name = sanitize_output_name(payload.output_name)
        output_subdir = sanitize_subdir(payload.output_subdir)
        headers = validate_headers(payload.headers)
        if not any(name.lower() == "referer" for name in headers):
            headers["Referer"] = infer_referer(url)
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    fingerprint = _fingerprint(
        url, output_name, output_subdir, headers, payload.ignore_certificate_errors
    )
    if not payload.force and (existing := _existing_task(fingerprint)):
        return existing

    task = DownloadTask(
        source_type=SourceType.URL.value,
        source=url,
        headers=headers,
        source_fingerprint=fingerprint,
        output_name=output_name,
        output_subdir=output_subdir,
        ignore_certificate_errors=payload.ignore_certificate_errors,
    )
    return task_store.add(task)


@router.post("/tasks/upload", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_task(
    file: UploadFile = File(...),
    base_url: str | None = Form(default=None),
    output_name: str = Form(default="video"),
    output_subdir: str = Form(default=""),
    headers_json: str = Form(default="{}"),
    ignore_certificate_errors: bool = Form(default=False),
    force: bool = Form(default=False),
) -> DownloadTask:
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large"
        )
    try:
        loaded_headers = json.loads(headers_json)
        if not isinstance(loaded_headers, dict):
            raise ValueError("headers_json must be a JSON object")
        headers = validate_headers({str(key): str(value) for key, value in loaded_headers.items()})
        safe_name = sanitize_output_name(output_name)
        safe_subdir = sanitize_subdir(output_subdir)
        normalized = normalize_uploaded_playlist(content, base_url)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _bad_request(str(exc)) from exc

    fingerprint = _fingerprint(
        normalized, safe_name, safe_subdir, headers, ignore_certificate_errors
    )
    if not force and (existing := _existing_task(fingerprint)):
        return existing

    task = DownloadTask(
        source_type=SourceType.UPLOAD.value,
        source=file.filename or "upload.m3u8",
        headers=headers,
        source_fingerprint=fingerprint,
        output_name=safe_name,
        output_subdir=safe_subdir,
        base_url=base_url,
        ignore_certificate_errors=ignore_certificate_errors,
    )
    task_store.add(task)
    work_dir = settings.work_root / task.id
    work_dir.mkdir(parents=True, exist_ok=True)
    upload_path = work_dir / "upload.m3u8"
    upload_path.write_text(normalized, encoding="utf-8")
    return task_store.update(task.id, upload_path=str(upload_path)) or task


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> TaskListResponse:
    items, total = task_store.paginate(page, page_size)
    return TaskListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> DownloadTask:
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str) -> DownloadTask:
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status == TaskStatus.QUEUED.value:
        task_store.update(task.id, status=TaskStatus.CANCELLED.value)
    elif task.status in {
        TaskStatus.PREPARING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.POSTPROCESSING.value,
        TaskStatus.RETRY_WAIT.value,
    }:
        task_store.update(task.id, status=TaskStatus.CANCELLING.value)
    elif task.status not in {TaskStatus.CANCELLED.value, TaskStatus.FAILED.value}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task cannot be cancelled")
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskResponse)
def retry_task(task_id: str) -> DownloadTask:
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only failed or cancelled tasks can retry"
        )
    return task_store.update(
        task.id,
        status=TaskStatus.QUEUED.value,
        error_code=None,
        error_message=None,
    ) or task


@router.delete("/tasks/{task_id}", response_model=DeleteResponse)
def delete_task(
    task_id: str,
    delete_output: bool = Query(default=False),
) -> DeleteResponse:
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status in {
        TaskStatus.PREPARING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.POSTPROCESSING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.CANCELLING.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Cancel the task before deleting it"
        )

    output_deleted = False
    if delete_output and task.output_path:
        output = safe_output_path(settings.download_root, "", task.output_path)
        output.unlink(missing_ok=True)
        output_deleted = True
    shutil.rmtree(settings.work_root / task.id, ignore_errors=True)
    (settings.log_root / f"{task.id}.log").unlink(missing_ok=True)
    task_store.delete(task.id)
    return DeleteResponse(deleted=True, output_deleted=output_deleted)


@router.get("/tasks/{task_id}/logs", response_class=PlainTextResponse)
def task_logs(task_id: str, tail: int = Query(default=200, ge=1, le=2000)) -> str:
    path = settings.log_root / f"{task_id}.log"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:])


@router.get("/tasks/{task_id}/file", response_class=FileResponse)
def task_file(task_id: str) -> FileResponse:
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status != TaskStatus.COMPLETED.value or not task.output_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Task has no completed output"
        )
    path = safe_output_path(settings.download_root, "", task.output_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Output file not found")
    return FileResponse(path, media_type="video/mp4", filename=Path(task.output_path).name)
