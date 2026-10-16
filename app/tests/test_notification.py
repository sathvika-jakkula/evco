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
def override_auth_dependency(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "hunter2")
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


@pytest.mark.parametrize("content_type", [None, "text/plain", "text/html"])
@pytest.mark.parametrize("standalone", [False, True])
def test_email_content_type_reaches_smtp(monkeypatch, content_type, standalone):
    payload = {
        "to": ["a@example.com"],
        "subject": "Quote ready",
        "body": "<h1>Quote ready</h1><p>Total: €120</p>",
    }
    if content_type is not None:
        payload["content_type"] = content_type

    if standalone:
        from scripts import notification_service as script

        monkeypatch.setattr(script, "SMTP_USER", "sender@example.com")
        monkeypatch.setattr(script, "SMTP_PASSWORD", "test-password")
        test_client = TestClient(script.app)
        endpoint = "/send-email"
    else:
        from app.modules.notification import router

        monkeypatch.setattr(router, "notification_service", NotificationService(MagicMock(
            log_notification=MagicMock(return_value=uuid4())
        )))
        test_client = client
        endpoint = "/notifications/send-email"

    with patch("smtplib.SMTP") as smtp:
        response = test_client.post(endpoint, json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["delivery_status"] == "SENT"
        server = smtp.return_value.__enter__.return_value
        message = server.send_message.call_args.args[0]
        assert message.get_content_type() == (content_type or "text/plain")
        assert message.get_content().rstrip("\n") == payload["body"]


@pytest.mark.parametrize("standalone", [False, True])
def test_unsupported_email_content_type_rejected(standalone):
    if standalone:
        from scripts.notification_service import app as script_app

        test_client, endpoint = TestClient(script_app), "/send-email"
        expected_status = 422
    else:
        test_client, endpoint = client, "/notifications/send-email"
        expected_status = 400

    with patch("smtplib.SMTP") as smtp:
        response = test_client.post(endpoint, json={
            "to": ["a@example.com"], "subject": "Test", "body": "Body",
            "content_type": "application/pdf",
        })
        assert response.status_code == expected_status
        smtp.assert_not_called()
