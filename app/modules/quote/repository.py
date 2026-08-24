from typing import Optional, List
from app.modules.quote.models import QuoteModel


class QuoteRepository:
    def __init__(self) -> None:
        self._storage: dict[str, QuoteModel] = {}

    def save(self, quote: QuoteModel) -> None:
        self._storage[quote.id] = quote

    def get_by_id(self, quote_id: str) -> Optional[QuoteModel]:
        return self._storage.get(quote_id)

    def list_all(self) -> List[QuoteModel]:
        return list(self._storage.values())
