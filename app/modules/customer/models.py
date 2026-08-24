from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MatchStatus(str, Enum):
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class CustomerRecord:
    ar_custo_id: int
    customer_name: str
    customer_number: Optional[str] = None
    address: Optional[str] = None
