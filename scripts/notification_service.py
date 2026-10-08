"""
Standalone Notification Micro-Service
======================================
Self-contained FastAPI app with the same email notification logic as the main
EVCO backend. Deploy this independently with your own SMTP credentials for
internal testing / Darin demos. Later, swap in EVCO's real creds.

Usage:
    # 1. Set env vars (or use a .env in this directory)
    export SMTP_HOST="smtp.gmail.com"
    export SMTP_PORT="587"
    export SMTP_USER="your-email@gmail.com"
    export SMTP_PASSWORD="your-app-password"

    # 2. Run
    pip install fastapi uvicorn python-dotenv pydantic
    python notification_service.py

    # 3. Test -- no auth token needed
    curl -X POST http://localhost:8100/send-email \
      -H "Content-Type: application/json" \
      -d '{
        "to": ["recipient@example.com"],
        "subject": "Test from EVCO Notification Service",
        "body": "Hello from the standalone notification service."
      }'
"""

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent / ".env")

SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

PORT: int = int(os.getenv("NOTIFICATION_PORT", "8100"))

logger = logging.getLogger("notification-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


# ---------------------------------------------------------------------------
# Schemas  (mirrors app.modules.notification.schemas)
# ---------------------------------------------------------------------------
class SendEmailRequest(BaseModel):
    to: List[str] = Field(..., min_length=1, description="Recipient email address(es)")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")
    from_email: Optional[str] = Field(
        default=None,
        alias="from",
        description="Sender email. Falls back to SMTP_USER env var if omitted.",
    )
    processing_id: Optional[UUID] = Field(
        default=None, description="processing_id to associate this notification with, if any"
    )

    model_config = {"populate_by_name": True}


class RecipientResult(BaseModel):
    notification_id: UUID
    recipient: str
    delivery_status: str  # SENT | FAILED
    sent_time: Optional[datetime] = None
    error: Optional[str] = None


class SendEmailResponse(BaseModel):
    statusCode: int = 200
    message: str = "Notification processed"
    data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Core email logic  (mirrors app.modules.notification.service)
# ---------------------------------------------------------------------------
def send_email_smtp(
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipients: List[str],
    subject: str,
    body: str,
) -> List[RecipientResult]:
    """
    Connect to SMTP, authenticate once, send to each recipient.
    Returns per-recipient results.
    """
    results: List[RecipientResult] = []

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(username, password)

            for recipient in recipients:
                msg = EmailMessage()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = subject
                msg.set_content(body)

                sent_time = datetime.now(timezone.utc)
                try:
                    server.send_message(msg)
                    results.append(RecipientResult(
                        notification_id=uuid4(),
                        recipient=recipient,
                        delivery_status="SENT",
                        sent_time=sent_time,
                    ))
                    logger.info("[OK] Sent to %s", recipient)
                except Exception as exc:
                    logger.error("[FAIL] Failed sending to %s: %s", recipient, exc)
                    results.append(RecipientResult(
                        notification_id=uuid4(),
                        recipient=recipient,
                        delivery_status="FAILED",
                        sent_time=sent_time,
                        error=str(exc),
                    ))
    except Exception as conn_exc:
        logger.error("SMTP connection/auth failure (%s:%s): %s", host, port, conn_exc)
        sent_time = datetime.now(timezone.utc)
        for recipient in recipients:
            results.append(RecipientResult(
                notification_id=uuid4(),
                recipient=recipient,
                delivery_status="FAILED",
                sent_time=sent_time,
                error=str(conn_exc),
            ))

    return results


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="EVCO Notification Service (Standalone)",
    description="Self-contained notification micro-service for testing and demos.",
    version="1.0.0",
)


@app.get("/health", tags=["Health"])
async def health():
    """Health check."""
    return {"statusCode": 200, "message": "Notification service is healthy", "data": {"status": "healthy"}}





@app.post("/send-email", tags=["Notifications"], status_code=status.HTTP_200_OK)
async def send_email(payload: SendEmailRequest):
    """
    Send an email notification.

    Uses server-side SMTP credentials (SMTP_USER / SMTP_PASSWORD env vars).
    The caller only needs to provide `to`, `subject`, and `body`.
    Optionally override the sender with the `from` / `from_email` field.
    """
    sender = payload.from_email or SMTP_USER
    if not sender:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "statusCode": 400,
                "message": "No sender email configured. Set SMTP_USER env var or include 'from' in the request.",
                "data": None,
            },
        )

    if not SMTP_PASSWORD:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "statusCode": 500,
                "message": "SMTP_PASSWORD env var is not configured on this server.",
                "data": None,
            },
        )

    results = send_email_smtp(
        host=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USER or sender,
        password=SMTP_PASSWORD,
        sender=sender,
        recipients=payload.to,
        subject=payload.subject,
        body=payload.body,
    )

    statuses = {r.delivery_status for r in results}
    if statuses == {"SENT"}:
        overall = "SENT"
    elif statuses == {"FAILED"}:
        overall = "FAILED"
    else:
        overall = "PARTIAL"

    return {
        "statusCode": 200,
        "message": "Notification processed",
        "data": {
            "subject": payload.subject,
            "delivery_status": overall,
            "recipients": [r.model_dump(mode="json") for r in results],
        },
    }


@app.get("/config", tags=["Debug"])
async def show_config():
    """Show current SMTP config (password masked)."""
    return {
        "statusCode": 200,
        "message": "Current SMTP configuration",
        "data": {
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_user": SMTP_USER,
            "smtp_password": "***" if SMTP_PASSWORD else "(not set)",
        },
    }


# ---------------------------------------------------------------------------
# Run directly: python notification_service.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting standalone notification service on port %s", PORT)
    logger.info("SMTP config: host=%s, port=%s, user=%s", SMTP_HOST, SMTP_PORT, SMTP_USER)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
