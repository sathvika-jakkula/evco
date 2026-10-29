from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


# --- T21: Notification ---
class SendNotificationRequest(BaseModel):
    to: List[str] = Field(..., min_length=1, description="Recipient email address(es)")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")
    content_type: Literal["text/plain", "text/html"] = Field(
        default="text/plain", description="MIME type of the email body"
    )
    processing_id: Optional[UUID] = Field(
        default=None, description="processing_id to associate this notification with, if any"
    )
    client_report: Optional["ClientReportAttachment"] = Field(
        default=None,
        description=(
            "When set, the client review workbook (xlsx) for these scans and/or quotes is built and "
            "attached to the email. Omit to send the email without an attachment."
        ),
    )


class ClientReportAttachment(BaseModel):
    processing_ids: List[UUID] = Field(
        ..., min_length=1,
        description="processing_id of each quote to include in the workbook. One sheet per quote, in this order.",
    )
    file_name: str = Field(
        default="EVCO_Review_Per_Quote.xlsx", min_length=1, max_length=120,
        description="File name of the attached workbook",
    )


class NotificationRecipientResult(BaseModel):
    notification_id: UUID
    recipient: str
    delivery_status: str
    sent_time: Optional[datetime] = None
    error: Optional[str] = None


class NotificationAttachmentResult(BaseModel):
    file_name: str
    content_type: str
    size_bytes: int
    sheets: List[str]
    processing_count: int


class SendNotificationResponseData(BaseModel):
    subject: str
    delivery_status: str
    recipients: List[NotificationRecipientResult]
    attachments: List[NotificationAttachmentResult] = Field(default_factory=list)
