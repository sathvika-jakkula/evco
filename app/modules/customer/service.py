from app.modules.customer.models import MatchStatus
from app.modules.customer.repository import CustomerRepository
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.modules.customer.schemas import (
    CustomerCandidateSchema,
    CustomerResolveData,
    CustomerResolveRequest,
    CustomerResolveResponse,
    MatchStatusEnum,
)


class CustomerService:
    """Business logic service for customer resolution."""

    def __init__(
        self,
        repository: CustomerRepository | None = None,
        processing_repository: QuoteProcessingRepository | None = None,
    ) -> None:
        self.repository = repository or CustomerRepository()
        self.processing_repository = processing_repository or QuoteProcessingRepository()

    def resolve_customer(self, payload: CustomerResolveRequest) -> CustomerResolveResponse:
        records = self.repository.find_candidates(
            customer_name=payload.customer_name,
            customer_number=payload.customer_number,
        )

        candidates = [
            CustomerCandidateSchema(
                ar_custo_id=r.ar_custo_id,
                customer_name=r.customer_name,
                customer_number=r.customer_number,
                address=r.address,
            )
            for r in records
        ]

        if len(candidates) == 1:
            match_status = MatchStatusEnum.UNIQUE
        elif len(candidates) > 1:
            match_status = MatchStatusEnum.AMBIGUOUS
        else:
            match_status = MatchStatusEnum.NOT_FOUND

        resolution_id = None
        customer_exceptions: dict[str, str] = {}
        if match_status == MatchStatusEnum.AMBIGUOUS:
            customer_exceptions["EX-006"] = (
                "Customer is ambiguous: multiple possible customers or divisions were found; "
                "manual review is required."
            )
        elif (
            match_status == MatchStatusEnum.UNIQUE
            and payload.aka_customer_number
            and candidates[0].customer_number
            and payload.aka_customer_number.strip().upper() != candidates[0].customer_number.strip().upper()
        ):
            customer_exceptions["EX-005"] = (
                "Customer mismatch: the quote resolved to customer number "
                f"'{candidates[0].customer_number}', but the AKA record is tied to "
                f"'{payload.aka_customer_number}'. Processing must stop for this item."
            )

        exception_ids = []
        if payload.processing_id:
            candidate_data = [candidate.model_dump(mode="json") for candidate in candidates]
            if match_status == MatchStatusEnum.UNIQUE and not customer_exceptions:
                resolution_id = self.processing_repository.save_unique_customer_resolution_for_processing(
                    processing_id=payload.processing_id,
                    customer_name=payload.customer_name,
                    candidates=candidate_data,
                )
            if match_status == MatchStatusEnum.UNIQUE and candidates[0].customer_number:
                self.processing_repository.save_audit_customer_number(
                    processing_id=payload.processing_id,
                    customer_number=candidates[0].customer_number,
                )
            if customer_exceptions:
                exception_ids = self.processing_repository.save_customer_exceptions_for_processing(
                    processing_id=payload.processing_id,
                    exceptions=customer_exceptions,
                )

        message = "Customer resolution requires manual review" if customer_exceptions else "Customer resolution completed successfully"
        return CustomerResolveResponse(
            statusCode=200,
            message=message,
            data=CustomerResolveData(
                match_status=match_status,
                candidates=candidates,
                customer_resolution_id=resolution_id,
                exception_codes=list(customer_exceptions),
                exception_ids=exception_ids,
            ),
        )
