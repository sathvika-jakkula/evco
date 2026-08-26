from fastapi import APIRouter, status

from app.core.schemas import StandardResponse, success_response
from app.modules.notification.schemas import SendNotificationRequest, SendNotificationResponseData
from app.modules.notification.service import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post(
    "/send-email",
    response_model=StandardResponse[SendNotificationResponseData],
    status_code=status.HTTP_200_OK,
    summary="T21 Notification",
    description="Notify configured recipients via email with the provided content.",
)
async def send_email(payload: SendNotificationRequest):
    result = notification_service.send_email(payload)
    return success_response(
        data=result,
        message="Notification processed",
        status_code=status.HTTP_200_OK,
    )
