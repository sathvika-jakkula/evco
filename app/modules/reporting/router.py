from fastapi import APIRouter, Response, status

from app.core.schemas import StandardResponse, success_response
from app.modules.reporting.schemas import QuoteSummaryData, QuoteSummaryRequest
from app.modules.reporting.service import ReportingService

router = APIRouter(prefix="/reporting", tags=["Reporting"])
service = ReportingService()


@router.post(
    "/quote-summary",
    response_model=StandardResponse[QuoteSummaryData],
    status_code=status.HTTP_200_OK,
    summary="Quote Execution Summary data",
    description=(
        "Returns the aggregated per-line-item data (AKA/pricing/sales-order decisions, "
        "counts, exceptions) for a processing_id. Returns structured data only - no "
        "email text is generated; the caller formats and sends its own report."
    ),
)
async def get_quote_summary(payload: QuoteSummaryRequest, response: Response):
    data = service.get_quote_summary(payload.processing_id)
    response.status_code = status.HTTP_200_OK
    return success_response(
        data=data,
        message="Quote summary retrieved successfully",
        status_code=status.HTTP_200_OK,
    )
