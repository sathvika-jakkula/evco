from fastapi import APIRouter, Depends, status

from app.core.security import validate_access_token
from app.core.schemas import StandardResponse, success_response
from app.modules.monitoring.schemas import ScanRequest, ScanResponseData
from app.modules.monitoring.service import MonitoringService

router = APIRouter(prefix="/monitoring", dependencies=[Depends(validate_access_token)])
service = MonitoringService()


@router.post(
    "/scan",
    response_model=StandardResponse[ScanResponseData],
    status_code=status.HTTP_200_OK,
    summary="T1 Scan and Claim Quote Files",
    description="Scans an incoming folder and returns the PDF quotes it contains.",
)
async def scan_quote_files(payload: ScanRequest):
    scan_id, scan_timestamp, files_detected = service.scan_and_claim(
        folder_path=payload.folder_path,
        recursive=payload.recursive,
    )
    data = ScanResponseData(
        scan_id=scan_id,
        scan_timestamp=scan_timestamp,
        files_detected=files_detected,
        file_count=len(files_detected),
    )
    return success_response(
        data=data,
        message="Scan executed successfully",
        status_code=status.HTTP_200_OK,
    )
