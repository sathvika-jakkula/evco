from typing import List, Optional
from app.modules.audit.models import AuditRecord


class AuditRepository:
    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}

    def save(self, record: AuditRecord) -> None:
        self._records[record.id] = record

    def get_by_id(self, record_id: str) -> Optional[AuditRecord]:
        return self._records.get(record_id)

    def list_all(self) -> List[AuditRecord]:
        return list(self._records.values())
