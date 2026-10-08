from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


# --- T21: Notification ---
class SendNotificationRequest(BaseModel):
    to: List[str] = Field(..., min_length=1, description="Recipient email address(es)")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")
    processing_id: Optional[UUID] = Field(
        default=None, description="processing_id to associate this notification with, if any"
    )


class NotificationRecipientResult(BaseModel):
    notification_id: UUID
    recipient: str
    delivery_status: str
    sent_time: Optional[datetime] = None
    error: Optional[str] = None


class SendNotificationResponseData(BaseModel):
    subject: str
    delivery_status: str
    recipients: List[NotificationRecipientResult]
