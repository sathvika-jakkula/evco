from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QuoteModel:
    id: str
    quote_number: str
    customer_name: Optional[str] = None
    status: str = "active"
    parts: List[dict] = field(default_factory=list)
