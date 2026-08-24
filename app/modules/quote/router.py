from fastapi import APIRouter
from app.modules.quote.service import QuoteService

router = APIRouter()
service = QuoteService()
