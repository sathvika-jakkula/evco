from typing import List, Optional
from pydantic import BaseModel, Field
from app.modules.extraction.schemas import PartInfo


class QuoteCreate(BaseModel):
    quote_number: str
    customer_name: Optional[str] = None
    parts: List[PartInfo] = Field(default_factory=list)


class QuoteResponse(QuoteCreate):
    id: str
    status: str = "active"
