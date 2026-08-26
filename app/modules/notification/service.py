"""T21: generic email notification tool.

Per explicit instruction, SMTP account credentials (email + password) are
supplied in the request payload and used to authenticate for that one send -
they are never persisted or logged, only the SMTP server address (SMTP_HOST/
SMTP_PORT) is configured server-side.
"""

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import List
from uuid import UUID, uuid4

from app.core.config import settings
from app.database.notification_repository import NotificationRepository
from app.modules.notification.schemas import (
    NotificationRecipientResult,
    SendNotificationRequest,
    SendNotificationResponseData,
)

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, notification_repository: NotificationRepository | None = None) -> None:
        self.notification_repository = notification_repository or NotificationRepository()

    def send_email(self, req: SendNotificationRequest) -> SendNotificationResponseData:
        results: List[NotificationRecipientResult] = []
        password = req.password.get_secret_value()

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(req.from_email, password)
                for recipient in req.to:
                    results.append(self._send_one(server, req, recipient))
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

        return SendNotificationResponseData(subject=req.subject, delivery_status=overall, recipients=results)

    def _send_one(self, server: smtplib.SMTP, req: SendNotificationRequest, recipient: str) -> NotificationRecipientResult:
        message = EmailMessage()
        message["From"] = req.from_email
        message["To"] = recipient
        message["Subject"] = req.subject
        message.set_content(req.body)

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
