from typing import List, Optional

from app.modules.quote.mapper import dto_to_model, model_to_dto
from app.modules.quote.repository import QuoteRepository
from app.modules.quote.schemas import QuoteCreate, QuoteResponse
from app.modules.quote.validators import validate_quote


class QuoteService:
    def __init__(self, repository: QuoteRepository | None = None) -> None:
        self.repository = repository or QuoteRepository()

    def create_quote(self, dto: QuoteCreate) -> QuoteResponse:
        validate_quote(dto)
        model = dto_to_model(dto)
        self.repository.save(model)
        return model_to_dto(model)

    def get_quote(self, quote_id: str) -> Optional[QuoteResponse]:
        model = self.repository.get_by_id(quote_id)
        return model_to_dto(model) if model else None

    def list_quotes(self) -> List[QuoteResponse]:
        return [model_to_dto(m) for m in self.repository.list_all()]
