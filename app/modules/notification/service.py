"""T21: generic email notification tool.

SMTP credentials (SMTP_USER / SMTP_PASSWORD) are configured server-side via
environment variables. Callers supply to, subject, body, and optionally content_type.
The SMTP server address is also server-side (SMTP_HOST / SMTP_PORT).
"""

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import List, Optional
from uuid import UUID, uuid4

from app.core.config import settings
from app.database.notification_repository import NotificationRepository
from app.modules.reporting.schemas import ClientReportSource
from app.modules.reporting.service import CLIENT_REPORT_CONTENT_TYPE, ClientReportFile, ReportingService
from app.modules.notification.schemas import (
    NotificationAttachmentResult,
    NotificationRecipientResult,
    SendNotificationRequest,
    SendNotificationResponseData,
)

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        notification_repository: NotificationRepository | None = None,
        reporting_service: ReportingService | None = None,
    ) -> None:
        self.notification_repository = notification_repository or NotificationRepository()
        self.reporting_service = reporting_service or ReportingService()

    def send_email(self, req: SendNotificationRequest) -> SendNotificationResponseData:
        results: List[NotificationRecipientResult] = []
        sender = settings.SMTP_USER
        password = settings.SMTP_PASSWORD

        # Build the workbook before connecting to SMTP. If it fails, nothing is sent.
        attachment: Optional[ClientReportFile] = None
        if req.client_report is not None:
            source = ClientReportSource(processing_ids=req.client_report.processing_ids)
            attachment = self.reporting_service.build_client_report(source, req.client_report.file_name)
        attachments = [NotificationAttachmentResult(
            file_name=attachment.file_name,
            content_type=CLIENT_REPORT_CONTENT_TYPE,
            size_bytes=len(attachment.content),
            sheets=attachment.sheet_names,
            processing_count=len(attachment.processing_ids),
        )] if attachment else []

        if not sender or not password:
            logger.error("SMTP_USER or SMTP_PASSWORD not configured in environment")
            sent_time = datetime.now(timezone.utc)
            for recipient in req.to:
                results.append(NotificationRecipientResult(
                    notification_id=self._log(req, recipient, "FAILED", sent_time),
                    recipient=recipient,
                    delivery_status="FAILED",
                    sent_time=sent_time,
                    error="SMTP credentials not configured on server (SMTP_USER / SMTP_PASSWORD)",
                ))
            return SendNotificationResponseData(subject=req.subject, delivery_status="FAILED", recipients=results,
                                                attachments=attachments)

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(sender, password)
                for recipient in req.to:
                    results.append(self._send_one(server, req, sender, recipient, attachment))
        except Exception as connect_exc:
            # Could not even connect/authenticate to the SMTP server - log a
            # FAILED row per intended recipient so the failure is still auditable.
            logger.error("Failed to connect/authenticate to SMTP server %s:%s: %s",
                         settings.SMTP_HOST, settings.SMTP_PORT, connect_exc)
            sent_time = datetime.now(timezone.utc)
            for recipient in req.to:
                results.append(NotificationRecipientResult(
                    notification_id=self._log(req, recipient, "FAILED", sent_time),
                    recipient=recipient,
                    delivery_status="FAILED",
                    sent_time=sent_time,
                    error=str(connect_exc),
                ))

        statuses = {result.delivery_status for result in results}
        if statuses == {"SENT"}:
            overall = "SENT"
        elif statuses == {"FAILED"}:
            overall = "FAILED"
        else:
            overall = "PARTIAL"

        return SendNotificationResponseData(subject=req.subject, delivery_status=overall, recipients=results,
                                            attachments=attachments)

    def _send_one(
        self, server: smtplib.SMTP, req: SendNotificationRequest, sender: str, recipient: str,
        attachment: Optional[ClientReportFile] = None,
    ) -> NotificationRecipientResult:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = req.subject
        message.set_content(req.body, subtype=req.content_type.split("/")[1])
        if attachment is not None:
            message.add_attachment(
                attachment.content,
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=attachment.file_name,
            )

        sent_time = datetime.now(timezone.utc)
        try:
            server.send_message(message)
            delivery_status, error = "SENT", None
        except Exception as send_exc:
            logger.error("Failed to send notification to %s: %s", recipient, send_exc)
            delivery_status, error = "FAILED", str(send_exc)

        return NotificationRecipientResult(
            notification_id=self._log(req, recipient, delivery_status, sent_time),
            recipient=recipient,
            delivery_status=delivery_status,
            sent_time=sent_time,
            error=error,
        )

    def _log(self, req: SendNotificationRequest, recipient: str, delivery_status: str, sent_time: datetime) -> UUID:
        try:
            return self.notification_repository.log_notification(
                recipient=recipient,
                notification_type="EMAIL",
                subject=req.subject,
                message=req.body,
                delivery_status=delivery_status,
                processing_id=req.processing_id,
                sent_time=sent_time,
            )
        except Exception as exc:
            logger.warning("Failed to write sent_notifications entry for %s: %s", recipient, exc)
            return uuid4()


# Singleton instance, mirroring the other services in this codebase
notification_service = NotificationService()

