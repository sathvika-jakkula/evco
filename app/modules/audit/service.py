import uuid
from typing import List, Optional

from app.modules.audit.models import AuditRecord
from app.modules.audit.repository import AuditRepository
from app.modules.audit.schemas import AuditLogCreate, AuditLogResponse


class AuditService:
    def __init__(self, repository: AuditRepository | None = None) -> None:
        self.repository = repository or AuditRepository()

    def log_action(self, dto: AuditLogCreate) -> AuditLogResponse:
        record = AuditRecord(
            id=str(uuid.uuid4()),
            action=dto.action,
            user_id=dto.user_id,
            details=dto.details,
        )
        self.repository.save(record)
        return AuditLogResponse(
            id=record.id,
            action=record.action,
            user_id=record.user_id,
            details=record.details,
            timestamp=record.timestamp,
        )

    def get_log(self, record_id: str) -> Optional[AuditLogResponse]:
        record = self.repository.get_by_id(record_id)
        if not record:
            return None
        return AuditLogResponse(
            id=record.id,
            action=record.action,
            user_id=record.user_id,
            details=record.details,
            timestamp=record.timestamp,
        )

    def list_logs(self) -> List[AuditLogResponse]:
        return [
            AuditLogResponse(
                id=r.id,
                action=r.action,
                user_id=r.user_id,
                details=r.details,
                timestamp=r.timestamp,
            )
            for r in self.repository.list_all()
        ]
