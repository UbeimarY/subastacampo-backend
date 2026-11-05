from typing import Any

import httpx
import pytest

from subastacampo.v1 import common_pb2
from tests.rpc import call, error_detail

pytestmark = pytest.mark.anyio

BUYER = {
    "fullName": "Ana Compradora",
    "email": "Ana@Example.com",
    "password": "very-secure-1",
    "role": "USER_ROLE_BUYER",
    "municipality": "Pasto",
}


async def register(api: httpx.AsyncClient, **overrides: Any) -> httpx.Response:
    return await call(api, "AuthService", "Register", {**BUYER, **overrides})


async def login(api: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await call(api, "AuthService", "Login", {"email": email, "password": password})


async def test_register_returns_the_user_without_the_password(api: httpx.AsyncClient) -> None:
    response = await register(api)

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["email"] == "ana@example.com"
    assert user["role"] == "USER_ROLE_BUYER"
    assert "password" not in user


async def test_register_rejects_a_duplicate_email_case_insensitively(
    api: httpx.AsyncClient,
) -> None:
    await register(api)

    response = await register(api, email="ANA@example.com")

    assert response.status_code == 409
    assert error_detail(response).reason == common_pb2.ERROR_REASON_EMAIL_ALREADY_REGISTERED


async def test_register_validates_the_password_length(api: httpx.AsyncClient) -> None:
    response = await register(api, password="short")

    assert response.status_code == 400
    detail = error_detail(response)
    assert detail.reason == common_pb2.ERROR_REASON_VALIDATION_FAILED
    assert detail.field == "password"


async def test_login_returns_a_token_that_identifies_the_user(api: httpx.AsyncClient) -> None:
    await register(api)

    response = await login(api, "ana@example.com", "very-secure-1")
    assert response.status_code == 200
    token = response.json()["accessToken"]

    me = await call(api, "AuthService", "GetCurrentUser", token=token)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ana@example.com"


async def test_wrong_password_and_unknown_email_get_the_same_error(
    api: httpx.AsyncClient,
) -> None:
    await register(api)

    wrong_password = await login(api, "ana@example.com", "wrong-password")
    unknown_email = await login(api, "nobody@example.com", "very-secure-1")

    for response in (wrong_password, unknown_email):
        assert response.status_code == 401
        assert error_detail(response).reason == common_pb2.ERROR_REASON_INVALID_CREDENTIALS


async def test_current_user_requires_a_token(api: httpx.AsyncClient) -> None:
    response = await call(api, "AuthService", "GetCurrentUser")

    assert response.status_code == 401
    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUTHENTICATION_REQUIRED


async def test_a_tampered_token_is_rejected(api: httpx.AsyncClient) -> None:
    response = await call(api, "AuthService", "GetCurrentUser", token="not.a.valid-token")

    assert response.status_code == 401
    assert error_detail(response).reason == common_pb2.ERROR_REASON_SESSION_EXPIRED
