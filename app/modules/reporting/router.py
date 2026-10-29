from fastapi import APIRouter, Response, status
from fastapi.responses import Response as FileResponse

from app.core.schemas import StandardResponse, success_response
from app.modules.reporting.schemas import ClientReportRequest, QuoteSummaryData, QuoteSummaryRequest
from app.modules.reporting.service import ReportingService

router = APIRouter(prefix="/reporting", tags=["Reporting"])
service = ReportingService()

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
def get_quote_summary(payload: QuoteSummaryRequest, response: Response):
    data = service.get_quote_summary(payload.processing_id)
    response.status_code = status.HTTP_200_OK
    return success_response(
        data=data,
        message="Quote summary retrieved successfully",
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/client-report",
    status_code=status.HTTP_200_OK,
    summary="Client review workbook (xlsx)",
    description=(
        "Builds the client review workbook for one or more scan_ids and/or processing_ids as an Excel file: "
        "a Summary sheet (total rows, update required, no action per quote) plus one sheet per "
        "quote listing every field-level finding. Response is the .xlsx file itself, not the "
        "StandardResponse envelope. Status 'Update' means a change is needed in IQMS; comments "
        "ending in *_WRITE_GAP must be made by hand in IQMS because the WebAPI has no write endpoint."
    ),
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "EVCO review workbook (.xlsx)"}},
)
def get_client_report(payload: ClientReportRequest):
    report = service.build_client_report(payload)
    return FileResponse(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{report.file_name}"'},
    )
