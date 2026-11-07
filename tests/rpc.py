"""Helpers to call Connect RPCs over HTTP with JSON, as a browser would."""

import base64
from typing import Any

import httpx

from subastacampo.v1.common_pb2 import ErrorDetail


async def call(
    client: httpx.AsyncClient,
    service: str,
    method: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post(
        f"/subastacampo.v1.{service}/{method}", json=body or {}, headers=headers
    )


def error_detail(response: httpx.Response) -> ErrorDetail:
    """Decodes the ErrorDetail the server attached to a Connect error."""
    value: str = response.json()["details"][0]["value"]
    padded = value + "=" * (-len(value) % 4)
    return ErrorDetail.FromString(base64.b64decode(padded))


async def sign_up(client: httpx.AsyncClient, *, role: str, email: str) -> str:
    """Registers a user and returns an access token."""
    password = "very-secure-1"
    await call(
        client,
        "AuthService",
        "Register",
        {"fullName": "Usuario Prueba", "email": email, "password": password, "role": role},
    )
    response = await call(client, "AuthService", "Login", {"email": email, "password": password})
    return str(response.json()["accessToken"])
