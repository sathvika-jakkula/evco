import uuid
from app.modules.quote.models import QuoteModel
from app.modules.quote.schemas import QuoteCreate, QuoteResponse


def dto_to_model(dto: QuoteCreate) -> QuoteModel:
    return QuoteModel(
        id=str(uuid.uuid4()),
        quote_number=dto.quote_number,
        customer_name=dto.customer_name,
        parts=[p.model_dump() for p in dto.parts],
    )


def model_to_dto(model: QuoteModel) -> QuoteResponse:
    return QuoteResponse(
        id=model.id,
        quote_number=model.quote_number,
        customer_name=model.customer_name,
        status=model.status,
        parts=model.parts,
    )
