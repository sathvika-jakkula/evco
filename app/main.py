import logging
from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.constants import APP_DESCRIPTION, APP_TITLE, APP_VERSION
from app.core.exceptions import APIException
from app.core.logging import configure_logging
from app.core.middleware import register_cors
from app.core.schemas import ErrorCategory, error_response, success_response

logger = logging.getLogger(__name__)

configure_logging()

docs_url = "/docs" if not settings.PROTECT_DOCS else None
redoc_url = "/redoc" if not settings.PROTECT_DOCS else None
openapi_url = "/openapi.json" if not settings.PROTECT_DOCS else None

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)


# Exception Handlers enforcing Standard Error Schema
@app.exception_handler(APIException)
async def api_exception_handler(request, exc: APIException):
    payload = error_response(
        message=exc.message,
        code=exc.code,
        category=exc.category,
        status_code=exc.status_code,
        retryable=exc.retryable,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    category = ErrorCategory.SECURITY if exc.status_code in (401, 403) else ErrorCategory.BUSINESS
    code = "UNAUTHORIZED" if exc.status_code == 401 else ("FORBIDDEN" if exc.status_code == 403 else "HTTP_ERROR")
    payload = error_response(
        message=str(exc.detail),
        code=code,
        category=category,
        status_code=exc.status_code,
        retryable=False,
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    payload = error_response(
        message="Unable to process request due to validation errors",
        code="VALIDATION_ERROR",
        category=ErrorCategory.BUSINESS,
        status_code=400,
        retryable=False,
        details={"errors": exc.errors()},
    )
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=payload.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    payload = error_response(
        message="An unexpected server error occurred",
        code="INTERNAL_SERVER_ERROR",
        category=ErrorCategory.TECHNICAL,
        status_code=500,
        retryable=True,
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump())


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "IBM App ID OAuth 2.0 Access Token (RS256)",
    }
    openapi_schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

register_cors(app)


# Public Health Check Endpoint
@app.get("/health", tags=["Health"], include_in_schema=True)
async def health_check():
    """Public health check endpoint for load balancers and infrastructure monitoring."""
    return success_response(data={"status": "healthy"}, message="Service is healthy")


# Protect business API routes (includes inventory router with validate_access_token)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def docs_redirect():
    """Redirect root path to Swagger docs if enabled, or health check."""
    if not settings.PROTECT_DOCS:
        return RedirectResponse(url="/docs")
    return RedirectResponse(url="/health")
