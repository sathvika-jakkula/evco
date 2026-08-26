from fastapi import APIRouter, Depends

from app.core.security import validate_access_token
from app.modules.audit.router import router as audit_router
from app.modules.customer.router import router as customer_router
from app.modules.extraction.router import router as extraction_router
from app.modules.quote.router import router as quote_router

from app.modules.inventory.router import router as inventory_router
from app.modules.monitoring.router import router as monitoring_router
from app.modules.notification.router import router as notification_router
from app.modules.pricing.router import router as pricing_router
from app.modules.quote_processing.router import router as quote_processing_router
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
