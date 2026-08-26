from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import validate_access_token
from app.modules.notification.schemas import SendNotificationRequest
from app.modules.notification.service import NotificationService

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_dependency():
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "test-user-id"}
    yield
    app.dependency_overrides.clear()


def test_send_email_success_logs_sent_per_recipient():
    fake_repo = MagicMock()
    fake_repo.log_notification.return_value = uuid4()
    service = NotificationService(notification_repository=fake_repo)

    req = SendNotificationRequest(
        **{
            "from": "sender@example.com",
            "password": "hunter2",
            "to": ["a@example.com", "b@example.com"],
            "subject": "Quote processed",
            "body": "Your quote has been processed successfully.",
        }
    )

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        result = service.send_email(req)

    assert result.delivery_status == "SENT"
    assert len(result.recipients) == 2
    assert all(r.delivery_status == "SENT" for r in result.recipients)
    mock_server.login.assert_called_once_with("sender@example.com", "hunter2")
    assert mock_server.send_message.call_count == 2
    assert fake_repo.log_notification.call_count == 2
    # Password must never reach the persistence layer
    for call in fake_repo.log_notification.call_args_list:
        assert "hunter2" not in str(call)


def test_send_email_connection_failure_logs_failed_per_recipient():
    fake_repo = MagicMock()
    fake_repo.log_notification.return_value = uuid4()
    service = NotificationService(notification_repository=fake_repo)

    req = SendNotificationRequest(
        **{
            "from": "sender@example.com",
            "password": "hunter2",
            "to": ["a@example.com"],
            "subject": "Quote failed",
            "body": "Body",
        }
    )

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("no route to host")):
        result = service.send_email(req)

    assert result.delivery_status == "FAILED"
    assert result.recipients[0].delivery_status == "FAILED"
    assert result.recipients[0].error is not None
    fake_repo.log_notification.assert_called_once()


def test_send_email_endpoint(monkeypatch):
    import app.modules.notification.router as notification_router

    fake_service = MagicMock()
    from app.modules.notification.schemas import NotificationRecipientResult, SendNotificationResponseData
    from datetime import datetime, timezone

    fake_service.send_email.return_value = SendNotificationResponseData(
        subject="Hi",
        delivery_status="SENT",
        recipients=[
            NotificationRecipientResult(
                notification_id=uuid4(), recipient="a@example.com",
                delivery_status="SENT", sent_time=datetime.now(timezone.utc), error=None,
            )
        ],
    )
    monkeypatch.setattr(notification_router, "notification_service", fake_service)

    response = client.post(
        "/notifications/send-email",
        json={
            "from": "sender@example.com",
            "password": "hunter2",
            "to": ["a@example.com"],
            "subject": "Hi",
            "body": "Body",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["delivery_status"] == "SENT"
    # The response must never echo the password back
    assert "hunter2" not in response.text


def test_send_email_requires_auth():
    app.dependency_overrides.clear()
    response = client.post(
        "/notifications/send-email",
        json={"from": "a@example.com", "password": "x", "to": ["b@example.com"], "subject": "s", "body": "b"},
    )
    assert response.status_code == 401
