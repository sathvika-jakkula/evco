from fastapi import APIRouter
from app.modules.audit.service import AuditService

router = APIRouter()
service = AuditService()
