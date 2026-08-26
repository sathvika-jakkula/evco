"""Persistence for the sent_notifications table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class NotificationRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def log_notification(
        self,
        recipient: str,
        notification_type: str,
        subject: str,
        message: str,
        delivery_status: str,
        processing_id: Optional[UUID] = None,
        sent_time: Optional[datetime] = None,
    ) -> UUID:
        notification_id = uuid4()
        sent_time = sent_time or datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO sent_notifications (notification_id, processing_id, recipient,
                   notification_type, subject, message, delivery_status, sent_time)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    notification_id, processing_id, recipient, notification_type,
                    subject, message, delivery_status, sent_time,
                ),
            )
        return notification_id
