from fastapi import APIRouter, Depends

from app.core.security import validate_access_token
from app.modules.audit.router import router as audit_router
from app.modules.customer.router import router as customer_router
from app.modules.extraction.router import router as extraction_router
from app.modules.quote.router import router as quote_router

from app.modules.inventory.router import router as inventory_router
from app.modules.inventory.store import inventory_store
from app.modules.monitoring.router import router as monitoring_router
from app.modules.notification.router import router as notification_router
from app.modules.pricing.router import router as pricing_router
from app.modules.pricing.service import price_break_service
from app.modules.quote_processing.router import router as quote_processing_router
from app.modules.reporting.router import router as reporting_router
from app.modules.sales_order.router import router as sales_order_router

api_router = APIRouter(dependencies=[Depends(validate_access_token)])

api_router.include_router(customer_router, tags=["Customer"])
api_router.include_router(extraction_router, prefix="/api", tags=["Extraction"])
api_router.include_router(quote_router, prefix="/quote", tags=["Quote"])
api_router.include_router(audit_router, prefix="/audit", tags=["Audit"])
api_router.include_router(inventory_router, tags=["Inventory AKA Workflow"])
api_router.include_router(pricing_router, tags=["Inventory Price Breaks"])
api_router.include_router(monitoring_router, tags=["Monitoring"])
api_router.include_router(sales_order_router, tags=["Sales Orders"])
api_router.include_router(quote_processing_router, tags=["Quote Processing Results"])
api_router.include_router(notification_router, tags=["Notifications"])
api_router.include_router(reporting_router, tags=["Reporting"])


# ---------------------------------------------------------------------------
# Universal test-reset endpoint — resets ALL in-memory mock stores at once.
# ---------------------------------------------------------------------------
testing_router = APIRouter(prefix="/testing", tags=["Testing Utilities"])


@testing_router.post(
    "/reset-all",
    summary="Reset all mock stores (testing only)",
    description=(
        "Resets ALL in-memory mock stores (AKA inventory + pricing) back to their "
        "original seed data from aka_inventory.json. Use this between E2E test runs."
    ),
)
async def reset_all_stores():
    aka_result = inventory_store.reset()
    pricing_result = price_break_service.reset()
    return {
        "message": "All mock stores reset to original seed data",
        "inventory": {
            "seeded_items": aka_result["items"],
            "seeded_aka_records": aka_result["aka_records"],
        },
        "pricing": {
            "seeded_contexts": pricing_result["contexts"],
            "seeded_price_break_records": pricing_result["price_break_records"],
        },
    }


api_router.include_router(testing_router)
