from fastapi import APIRouter, status

from app.core.schemas import StandardResponse, success_response
from app.modules.quote_processing.schemas import (
    ExceptionRecordData,
    MoveQuoteFileRequest,
    MoveQuoteFileResponseData,
    RecordExceptionRequest,
)
from app.modules.quote_processing.service import quote_processing_result_service

router = APIRouter(prefix="/quote-processing", tags=["Quote Processing Results"])


@router.post(
    "/record-exception",
    response_model=StandardResponse[ExceptionRecordData],
    status_code=status.HTTP_201_CREATED,
    summary="T17 Record Exceptions",
    description=(
        "Persist a technical, business, or manual-review exception with batch (processing_id), "
        "quote-line, agent, and rule context."
    ),
)
async def record_exception(payload: RecordExceptionRequest):
    result = quote_processing_result_service.record_exception(payload)
    return success_response(
        data=result,
        message="Exception recorded successfully",
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/move-quote-file",
    response_model=StandardResponse[MoveQuoteFileResponseData],
    status_code=status.HTTP_200_OK,
    summary="T22 Move to Exceptions/Processed folder",
    description="Final step - moves a quote file from from_folder into to_folder.",
)
async def move_quote_file(payload: MoveQuoteFileRequest):
    result = quote_processing_result_service.move_quote_file(payload)
    return success_response(
        data=result,
        message="Quote file moved successfully",
        status_code=status.HTTP_200_OK,
    )
