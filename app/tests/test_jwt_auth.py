from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import verify_jwt_token

# Test setup variables
TEST_REGION = "us-south"
TEST_TENANT_ID = "test-tenant-12345"
TEST_CLIENT_ID = "test-client-67890"
TEST_ISSUER = f"https://{TEST_REGION}.appid.cloud.ibm.com/oauth/v4/{TEST_TENANT_ID}"


@pytest.fixture(scope="module")
def rsa_keys():
    """Generates RSA keypair for RS256 token signing in unit tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture(autouse=True)
def mock_app_settings(monkeypatch):
    """Configures settings for test environment."""
    monkeypatch.setattr(settings, "APPID_REGION", TEST_REGION)
    monkeypatch.setattr(settings, "APPID_TENANT_ID_RAW", TEST_TENANT_ID)
    monkeypatch.setattr(settings, "APPID_CLIENT_ID_RAW", TEST_CLIENT_ID)
    monkeypatch.setattr(settings, "APPID_OAUTH_SERVER_URL_RAW", TEST_ISSUER)
    monkeypatch.setattr(settings, "APPID_REQUIRED_SCOPE", "appid_default")
    monkeypatch.setattr(settings, "PROTECT_DOCS", False)


@pytest.fixture
def test_app(rsa_keys):
    """Creates isolated FastAPI app with test token validator."""
    private_key, public_key = rsa_keys

    def custom_token_validator(authorization: str | None = Header(default=None, alias="Authorization")) -> Dict[str, Any]:
        return verify_jwt_token(
            authorization=authorization,
            public_key_override=public_key,
        )

    def custom_scope_checker(payload: Dict[str, Any] = Depends(custom_token_validator)) -> Dict[str, Any]:
        token_scopes = payload.get("scope", "")
        scopes_list = token_scopes.split() if isinstance(token_scopes, str) else list(token_scopes)
        if "quote.read" not in scopes_list:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
        return payload

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    protected_router = APIRouter(dependencies=[Depends(custom_token_validator)])

    @protected_router.get("/protected")
    def protected_route():
        return {"message": "success"}

    @protected_router.get("/scoped", dependencies=[Depends(custom_scope_checker)])
    def scoped_route():
        return {"message": "scoped_success"}

    app.include_router(protected_router)
    return app


def create_test_jwt(
    private_key,
    iss: str = TEST_ISSUER,
    aud: str = TEST_CLIENT_ID,
    tenant: str = TEST_TENANT_ID,
    scope: str = "appid_default",
    exp_delta: timedelta = timedelta(hours=1),
    alg: str = "RS256",
    secret_key_override: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss,
        "aud": [aud] if aud else [],
        "sub": TEST_CLIENT_ID,
        "tenant": tenant,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
        "amr": ["appid_client_credentials"],
    }
    headers = {"kid": "test-key-id", "alg": alg}

    if alg == "RS256":
        return jwt.encode(payload, private_key, algorithm="RS256", headers=headers)
    elif alg == "HS256":
        key = secret_key_override or "secret"
        return jwt.encode(payload, key, algorithm="HS256", headers=headers)
    else:
        return jwt.encode(payload, "", algorithm=alg, headers=headers)


# 1. Public endpoint test
def test_public_health_endpoint(test_app):
    client = TestClient(test_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


# 2. Valid token -> 200
def test_valid_token_returns_200(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key)
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"message": "success"}


# 3. Missing Authorization header -> 401
def test_missing_authorization_header_returns_401(test_app):
    client = TestClient(test_app)
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 4. Invalid Bearer format -> 401
def test_invalid_bearer_format_returns_401(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key)
    client = TestClient(test_app)

    response1 = client.get("/protected", headers={"Authorization": f"Basic {token}"})
    assert response1.status_code == 401
    assert response1.json() == {"detail": "Invalid or expired access token"}

    response2 = client.get("/protected", headers={"Authorization": "Bearer"})
    assert response2.status_code == 401
    assert response2.json() == {"detail": "Invalid or expired access token"}


# 5. Malformed JWT -> 401
def test_malformed_jwt_returns_401(test_app):
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": "Bearer malformed.jwt.token"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 6. Expired JWT -> 401
def test_expired_jwt_returns_401(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, exp_delta=timedelta(hours=-1))
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 7. Invalid signature -> 401
def test_invalid_signature_returns_401(test_app):
    different_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = create_test_jwt(different_private_key)
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 8. Wrong issuer -> 401
def test_wrong_issuer_returns_401(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, iss="https://invalid-issuer.com")
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 9. Wrong audience -> 401
def test_wrong_audience_returns_401(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, aud="wrong-client-id")
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 10. Wrong tenant -> 401
def test_wrong_tenant_returns_401(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, tenant="wrong-tenant-id")
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 11. Unsupported algorithm (HS256) -> 401
def test_unsupported_algorithm_returns_401(test_app):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": TEST_ISSUER,
        "aud": [TEST_CLIENT_ID],
        "tenant": TEST_TENANT_ID,
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired access token"}


# 12. Valid JWT without required scope -> 403
def test_valid_jwt_without_required_scope_returns_403(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, scope="other_scope")
    client = TestClient(test_app)
    response = client.get("/scoped", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient scope"}


# 13. Valid JWT with required scope -> 200
def test_valid_jwt_with_required_scope_allowed(test_app, rsa_keys):
    private_key, _ = rsa_keys
    token = create_test_jwt(private_key, scope="appid_default quote.read")
    client = TestClient(test_app)
    response = client.get("/scoped", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"message": "scoped_success"}
