import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict

from fastapi import APIRouter, Header, status
from starlette.responses import JSONResponse

from app.core.schemas import StandardResponse, success_response
from app.modules.extraction.parser import (
    CallbackExtractionStatusResponse,
    ExtractionRequest,
    ProcessingResultRequest,
)
from app.modules.extraction.service import ExtractionService

router = APIRouter()
service = ExtractionService()


# Inline storage — replaces the old models.py + repository.py files
@dataclass
class ExtractionModel:
    file_name: str
    status: str = "pending"
    callback_url: str | None = None


class ExtractionRepository:
    def __init__(self) -> None:
        self._storage: dict[str, object] = {}

    def save(self, key: str, value: object) -> None:
        self._storage[key] = value

    def get(self, key: str):
        return self._storage.get(key)

    def save_pdf_reference(self, key: str, file_path) -> None:
        self._storage[key] = {"file_name": file_path.name, "path": str(file_path)}

    def get_pdf_reference(self, key: str):
        return self._storage.get(key)


repository = ExtractionRepository()


@router.post(
    "/extract-quote",
    response_model=StandardResponse[CallbackExtractionStatusResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Extract specific quote details and parts data",
    description=(
        "Locates the PDF quote document inside the configured folder using the provided filename, "
        "parses headers, and outputs grouped parts with multiple MOQ and pricing tiers. When a callback "
        "URL is supplied, the API responds immediately and posts the result back asynchronously."
    ),
)
async def extract_quote_pdf(
    payload: ExtractionRequest,
    callbackUrl: str = Header(..., alias="callbackUrl"),
):
    file_path = await service.resolve_pdf_file_path(payload.file_name, payload.folder_path)
    scan_file_id = service.resolve_scan_file_id(payload.scan_id, payload.file_name)
    processing_id = service.processing_repository.start_processing(file_path.name, scan_file_id=scan_file_id)
    repository.save_pdf_reference(payload.file_name, file_path)
    repository.save(
        payload.file_name,
        ExtractionModel(file_name=payload.file_name, status="accepted", callback_url=callbackUrl),
    )

    asyncio.create_task(service.run_callback_extraction(callbackUrl, file_path, processing_id))
    
    response_data = CallbackExtractionStatusResponse(
        status="accepted",
        message="Quote extraction started. Result will be sent to the callback URL.",
        callback_url=callbackUrl,
        processing_id=processing_id,
    )
    
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=success_response(
            data=response_data.model_dump(mode="json"),
            message="Quote extraction started. Result will be sent to the callback URL.",
            status_code=202,
        ).model_dump(mode="json"),
    )


@router.post(
    "/processing-result",
    response_model=StandardResponse[Dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Get the extracted quote data for a processing_id",
    description=(
        "Returns the extracted quote data for a given processing_id, in the same shape as the "
        "payload delivered to the extraction callback URL."
    ),
)
async def get_processing_result(payload: ProcessingResultRequest):
    quote_data = service.get_processing_result(payload.processing_id)
    return success_response(
        data=quote_data,
        message="Quote processing result retrieved successfully",
        status_code=status.HTTP_200_OK,
    )
