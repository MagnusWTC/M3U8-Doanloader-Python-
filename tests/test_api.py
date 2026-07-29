from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.security import infer_referer
from app.tasks import task_store


def test_api_does_not_require_token() -> None:
    task_store.clear()
    with TestClient(create_app(start_supervisor=False)) as client:
        response = client.get("/api/v1/tasks")

    assert response.status_code == 200
    assert response.json()["items"] == []


@patch("app.security.socket.getaddrinfo")
def test_url_submission_is_idempotent(mock_getaddrinfo: object) -> None:
    task_store.clear()
    mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]  # type: ignore[attr-defined]
    unique_url = f"https://example.test/{uuid4()}/master.m3u8"
    payload = {
        "url": unique_url,
        "output_name": "episode",
        "ignore_certificate_errors": True,
    }

    with TestClient(create_app(start_supervisor=False)) as client:
        first = client.post("/api/v1/tasks/url", json=payload)
        second = client.post("/api/v1/tasks/url", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert "max_height" not in first.json()
    assert first.json()["ignore_certificate_errors"] is True
    task_store.clear()


def test_infer_referer_uses_registrable_domain() -> None:
    assert (
        infer_referer("https://embeds02.example.streamsuperpro.com/path/master.m3u8")
        == "https://streamsuperpro.com/"
    )
    assert infer_referer("https://media.example.co.uk/video.m3u8") == "https://example.co.uk/"


@patch("app.security.socket.getaddrinfo")
def test_url_submission_infers_referer_without_overwriting_explicit_value(
    mock_getaddrinfo: object,
) -> None:
    task_store.clear()
    mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]  # type: ignore[attr-defined]

    with TestClient(create_app(start_supervisor=False)) as client:
        inferred = client.post(
            "/api/v1/tasks/url",
            json={"url": "https://cdn.example.co.uk/video.m3u8", "output_name": "inferred"},
        )
        explicit = client.post(
            "/api/v1/tasks/url",
            json={
                "url": "https://cdn.example.co.uk/other.m3u8",
                "output_name": "explicit",
                "headers": {"Referer": "https://player.example.org/watch/1"},
            },
        )

    assert inferred.status_code == 201
    assert explicit.status_code == 201
    assert task_store.get(inferred.json()["id"]).headers["Referer"] == "https://example.co.uk/"  # type: ignore[union-attr]
    assert task_store.get(explicit.json()["id"]).headers["Referer"] == "https://player.example.org/watch/1"  # type: ignore[union-attr]
    task_store.clear()
