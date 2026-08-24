from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import jwt
from jwt import PyJWKClient, PyJWKClientError, PyJWTError
from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


class JWKSManager:
    """Manager for PyJWKClient with key caching and URL management."""

    def __init__(self) -> None:
        self._client: Optional[PyJWKClient] = None
        self._jwks_url: Optional[str] = None

    def get_client(self, jwks_url: str | None = None) -> Optional[PyJWKClient]:
        target_url = jwks_url or (settings.APPID_JWKS_URL if settings.APPID_TENANT_ID else None)
        if not target_url:
            return None

        if self._client is None or self._jwks_url != target_url:
            self._jwks_url = target_url
            self._client = PyJWKClient(
                target_url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=settings.JWKS_CACHE_LIFESPAN,
                timeout=settings.JWKS_TIMEOUT,
            )
        return self._client


jwks_manager = JWKSManager()


def extract_bearer_token(authorization: str | None = Header(default=None, alias="Authorization")) -> str:
    """Extracts Bearer token string from Authorization header."""
    if not authorization:
        logger.warning("Authentication failed: Missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        logger.warning("Authentication failed: Invalid Authorization header format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token.strip()


def verify_jwt_token(
    authorization: str | None = None,
    jwk_client_override: Optional[PyJWKClient] = None,
    public_key_override: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Verifies IBM App ID OAuth 2.0 Bearer JWT using RS256 algorithm and JWKS public keys.
    Validates RS256 signature, exp, iat, iss, aud, tenant, and scope.
    """
    # Verify mandatory environment configuration
    if not settings.APPID_TENANT_ID or not settings.APPID_CLIENT_ID:
        logger.error("Authentication configuration error: APPID_TENANT_ID or APPID_CLIENT_ID is missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = extract_bearer_token(authorization)

    # 1. Inspect unverified header to enforce RS256 algorithm
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as exc:
        logger.warning("Authentication failed: Malformed JWT header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    alg = unverified_header.get("alg")
    if alg != "RS256":
        logger.warning("Authentication failed: Unsupported signing algorithm '%s'. Only RS256 is accepted.", alg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Retrieve signing key from JWKS or override
    signing_key = None
    if public_key_override is not None:
        signing_key = public_key_override
    else:
        client = jwk_client_override or jwks_manager.get_client()
        if not client:
            logger.error("Authentication configuration error: JWKS client initialization failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            signing_key = client.get_signing_key_from_jwt(token).key
        except (PyJWKClientError, Exception) as exc:
            logger.warning("Authentication failed: Unable to retrieve signing key from JWKS")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    # 3. Decode & Verify claims (signature, exp, iat, iss, aud)
    expected_issuer = settings.APPID_ISSUER
    expected_audience = settings.APPID_CLIENT_ID

    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=expected_audience,
            issuer=expected_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
                "require": ["exp", "iat", "iss", "aud"],
            },
        )
    except jwt.ExpiredSignatureError as exc:
        logger.warning("Authentication failed: Access token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidIssuerError as exc:
        logger.warning("Authentication failed: Invalid token issuer")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidAudienceError as exc:
        logger.warning("Authentication failed: Invalid token audience")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except PyJWTError as exc:
        logger.warning("Authentication failed: Invalid JWT (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # 4. Mandatory tenant claim verification
    token_tenant = payload.get("tenant")
    if not token_tenant or token_tenant != settings.APPID_TENANT_ID:
        logger.warning("Authentication failed: Token tenant claim missing or mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 5. Required scope verification
    if settings.APPID_REQUIRED_SCOPE:
        token_scopes = payload.get("scope", "")
        scopes_list = token_scopes.split() if isinstance(token_scopes, str) else list(token_scopes)
        if settings.APPID_REQUIRED_SCOPE not in scopes_list:
            logger.warning("Authorization failed: Missing required scope '%s'", settings.APPID_REQUIRED_SCOPE)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope",
            )

    logger.info("Authentication successful for subject '%s' (tenant: %s)", payload.get("sub"), token_tenant)
    return payload


def validate_access_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    """
    FastAPI dependency validating IBM App ID OAuth 2.0 Bearer JWT using RS256 algorithm.
    """
    return verify_jwt_token(authorization=authorization)


def require_scope(required_scope: str) -> Callable[..., Dict[str, Any]]:
    """
    Dependency factory to enforce scope authorization.
    Returns HTTP 403 Forbidden if the required scope is missing.
    """

    def scope_checker(payload: Dict[str, Any] = Depends(validate_access_token)) -> Dict[str, Any]:
        token_scopes = payload.get("scope", "")
        scopes_list = token_scopes.split() if isinstance(token_scopes, str) else list(token_scopes)
        if required_scope not in scopes_list:
            logger.warning("Authorization failed: Missing required scope '%s'", required_scope)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope",
            )
        return payload

    return scope_checker


def get_current_user(payload: Dict[str, Any] = Depends(validate_access_token)) -> Dict[str, Any]:
    """FastAPI dependency returning the current validated access token payload."""
    return payload


def get_auth_token(authorization: str | None = Header(default=None, alias="Authorization")) -> str:
    return extract_bearer_token(authorization)


def get_security_context() -> dict:
    return {"auth": "enabled", "provider": "ibm_appid", "algorithm": "RS256"}
