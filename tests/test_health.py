from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, cleanup_stale_work_root


def test_health() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_cleanup_only_removes_stale_work_files(tmp_path: Path) -> None:
    work_root = tmp_path / "data" / "work"
    log_root = tmp_path / "data" / "logs"
    download_root = tmp_path / "downloads"
    work_root.mkdir(parents=True)
    log_root.mkdir(parents=True)
    download_root.mkdir()
    (work_root / "stale.part").write_bytes(b"partial")
    (log_root / "task.log").write_text("log", encoding="utf-8")
    (download_root / "video.mp4").write_bytes(b"video")

    cleanup_stale_work_root(work_root)

    assert work_root.is_dir()
    assert list(work_root.iterdir()) == []
    assert (log_root / "task.log").exists()
    assert (download_root / "video.mp4").exists()
