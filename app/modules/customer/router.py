from fastapi import APIRouter, status
from app.modules.customer.schemas import CustomerResolveRequest, CustomerResolveResponse
from app.modules.customer.service import CustomerService

router = APIRouter()
service = CustomerService()


@router.post(
    "/customers/resolve",
    response_model=CustomerResolveResponse,
    status_code=status.HTTP_200_OK,
    summary="Resolve Customer",
    description="Resolves extracted customer name/number against DELMIAWorks customer records.",
)
async def resolve_customer(payload: CustomerResolveRequest) -> CustomerResolveResponse:
    return service.resolve_customer(payload)
