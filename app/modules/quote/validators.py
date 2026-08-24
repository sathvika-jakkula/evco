from app.modules.quote.schemas import QuoteCreate


def validate_quote(quote_data: QuoteCreate) -> bool:
    if not quote_data.quote_number.strip():
        raise ValueError("Quote number cannot be empty")
    return True
