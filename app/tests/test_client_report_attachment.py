from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.security import validate_access_token
from app.main import app
from app.modules.notification.schemas import ClientReportAttachment, SendNotificationRequest
from app.modules.notification.service import NotificationService
from app.modules.reporting import router as reporting_router
from app.modules.reporting.schemas import ClientReportRequest, ClientReportSource
from app.modules.reporting.service import ClientReportFile, ReportingService

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated():
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "t", "tenant": "t"}
    yield
    app.dependency_overrides.clear()


def _report_file(file_name="EVCO_Review_Per_Quote.xlsx", content=b"PK-test"):
    return ClientReportFile(file_name=file_name, content=content, sheet_names=["Summary", "Q1"],
                            processing_ids=[uuid4()])


def test_scan_ids_expand_to_processing_ids_without_duplicates():
    p1, p2, p3 = uuid4(), uuid4(), uuid4()
    scans = MagicMock()
    scans.get_processing_ids_for_scans.return_value = [p1, p2]
    service = ReportingService(scan_repository=scans, review_row_repository=MagicMock(),
                               line_item_audit_repository=MagicMock(), aka_resolution_repository=MagicMock(),
                               pricing_resolution_repository=MagicMock(),
                               sales_order_resolution_repository=MagicMock(),
                               pricing_history_repository=MagicMock())
    scan_id = uuid4()

    resolved = service.resolve_processing_ids(ClientReportSource(scan_ids=[scan_id], processing_ids=[p2, p3]))

    scans.get_processing_ids_for_scans.assert_called_once_with([scan_id])
    assert resolved == [p1, p2, p3]


def test_report_source_needs_scan_or_processing_ids():
    with pytest.raises(ValidationError):
        ClientReportSource()
    with pytest.raises(ValidationError):
        ClientReportAttachment(file_name="x.xlsx")


def test_notification_attachment_takes_processing_ids_only():
    with pytest.raises(ValidationError):
        ClientReportAttachment(scan_ids=[str(uuid4())])
    assert ClientReportAttachment(processing_ids=[str(uuid4())]).file_name == "EVCO_Review_Per_Quote.xlsx"


def test_send_email_attaches_the_workbook(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    reporting = MagicMock()
    reporting.build_client_report.return_value = _report_file("EVCO_Review_Per_Quote.xlsx", b"PK-test")
    smtp_cls = MagicMock()
    server = smtp_cls.return_value.__enter__.return_value
    monkeypatch.setattr("app.modules.notification.service.smtplib.SMTP", smtp_cls)
    repo = MagicMock()
    repo.log_notification.return_value = uuid4()
    service = NotificationService(notification_repository=repo, reporting_service=reporting)
    req = SendNotificationRequest(
        to=["ops@example.com"], subject="EVCO review", body="Attached.",
        client_report={"processing_ids": [str(uuid4())]},
    )

    result = service.send_email(req)

    assert result.attachments[0].file_name == "EVCO_Review_Per_Quote.xlsx"
    assert result.attachments[0].sheets == ["Summary", "Q1"]
    assert result.attachments[0].size_bytes == len(b"PK-test")
    sent = server.send_message.call_args.args[0]
    attachments = list(sent.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "EVCO_Review_Per_Quote.xlsx"
    assert attachments[0].get_payload(decode=True) == b"PK-test"


def test_failed_workbook_build_sends_no_email(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    reporting = MagicMock()
    reporting.build_client_report.side_effect = BusinessException(message="x", code="QUOTE_PROCESSING_NOT_FOUND",
                                                                  status_code=404)
    smtp_cls = MagicMock()
    monkeypatch.setattr("app.modules.notification.service.smtplib.SMTP", smtp_cls)
    service = NotificationService(notification_repository=MagicMock(), reporting_service=reporting)

    with pytest.raises(BusinessException):
        service.send_email(SendNotificationRequest(
            to=["ops@example.com"], subject="s", body="b", client_report={"processing_ids": [str(uuid4())]},
        ))
    smtp_cls.assert_not_called()


def test_email_without_client_report_has_no_attachment(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    smtp_cls = MagicMock()
    server = smtp_cls.return_value.__enter__.return_value
    monkeypatch.setattr("app.modules.notification.service.smtplib.SMTP", smtp_cls)
    repo = MagicMock()
    repo.log_notification.return_value = uuid4()
    service = NotificationService(notification_repository=repo, reporting_service=MagicMock())

    result = service.send_email(SendNotificationRequest(to=["ops@example.com"], subject="s", body="b"))

    assert result.attachments == []
    assert list(server.send_message.call_args.args[0].iter_attachments()) == []


def test_client_report_endpoint_accepts_scan_ids(monkeypatch):
    seen = {}

    def fake_build(source, file_name=None):
        seen["source"] = source
        return _report_file(content=b"PK-report")

    monkeypatch.setattr(reporting_router.service, "build_client_report", fake_build)
    scan_id = str(uuid4())

    response = client.post("/reporting/client-report", json={"scan_ids": [scan_id]})

    assert response.status_code == 200
    assert isinstance(seen["source"], ClientReportRequest)
    assert [str(s) for s in seen["source"].scan_ids] == [scan_id]
    assert response.content == b"PK-report"
    assert response.headers["content-disposition"] == 'attachment; filename="EVCO_Review_Per_Quote.xlsx"'
