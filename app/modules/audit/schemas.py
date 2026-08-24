from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AuditLogCreate(BaseModel):
    action: str
    user_id: Optional[str] = None
    details: Optional[str] = None


class AuditLogResponse(AuditLogCreate):
    id: str
    timestamp: datetime
