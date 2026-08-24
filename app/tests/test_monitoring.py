from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import validate_access_token
from app.main import app
import app.modules.monitoring.router as monitoring_router
from app.modules.monitoring.service import MonitoringService

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_dependency():
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "test-user-id"}
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fake_scan_repository(monkeypatch):
    fake_repository = MagicMock()
    monkeypatch.setattr(
        monitoring_router, "service", MonitoringService(scan_repository=fake_repository)
    )
    yield fake_repository


def test_scan_lists_only_pdfs(tmp_path: Path):
    (tmp_path / "55177-006A Clack Flow Control.pdf").write_bytes(b"pdf")
    (tmp_path / "notes.txt").write_text("not a quote", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "55847-076A.pdf").write_bytes(b"pdf")

    first = client.post("/monitoring/scan", json={"folder_path": str(tmp_path), "recursive": False})
    assert first.status_code == 200
    payload = first.json()
    assert payload["message"] == "Scan executed successfully"
    assert payload["data"]["files_detected"] == ["55177-006A Clack Flow Control.pdf"]
    assert payload["data"]["file_count"] == 1
    assert UUID(payload["data"]["scan_id"])
    assert not (tmp_path / ".monitoring_claims").exists()

    second = client.post("/monitoring/scan", json={"folder_path": str(tmp_path), "recursive": True})
    assert second.status_code == 200
    assert second.json()["data"]["files_detected"] == [
        "55177-006A Clack Flow Control.pdf",
        "nested/55847-076A.pdf",
    ]

    third = client.post("/monitoring/scan", json={"folder_path": str(tmp_path), "recursive": True})
    assert third.status_code == 200
    assert third.json()["data"]["files_detected"] == [
        "55177-006A Clack Flow Control.pdf",
        "nested/55847-076A.pdf",
    ]


def test_scan_returns_not_found_for_missing_folder(tmp_path: Path):
    response = client.post("/monitoring/scan", json={"folder_path": str(tmp_path / "missing"), "recursive": False})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MONITORED_FOLDER_NOT_FOUND"


def test_scan_requires_authentication(tmp_path: Path):
    app.dependency_overrides.clear()
    response = client.post("/monitoring/scan", json={"folder_path": str(tmp_path), "recursive": False})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
