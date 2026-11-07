"""Helpers to call Connect RPCs over HTTP with JSON, as a browser would."""

import asyncio
import base64
import json
from collections.abc import Callable
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


async def sign_up(
    client: httpx.AsyncClient, *, role: str, email: str, phone: str | None = None
) -> str:
    """Registers a user and returns an access token."""
    password = "very-secure-1"
    body: dict[str, Any] = {
        "fullName": "Usuario Prueba",
        "email": email,
        "password": password,
        "role": role,
    }
    if phone:
        body["phone"] = phone
    await call(client, "AuthService", "Register", body)
    response = await call(client, "AuthService", "Login", {"email": email, "password": password})
    return str(response.json()["accessToken"])


async def current_user_id(client: httpx.AsyncClient, token: str) -> str:
    response = await call(client, "AuthService", "GetCurrentUser", token=token)
    return str(response.json()["user"]["id"])


async def stream_call(
    client: httpx.AsyncClient,
    service: str,
    method: str,
    body: dict[str, Any],
    *,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Calls a server-streaming RPC with the Connect protocol and returns every message.

    Each message travels in an envelope: 1 flags byte, a 4-byte big-endian length,
    then the JSON payload. Flag 0x02 marks the final end-of-stream envelope.
    """
    payload = json.dumps(body).encode()
    headers = {"Content-Type": "application/connect+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.post(
        f"/subastacampo.v1.{service}/{method}",
        content=b"\x00" + len(payload).to_bytes(4, "big") + payload,
        headers=headers,
    )

    messages: list[dict[str, Any]] = []
    data, offset = response.content, 0
    while offset < len(data):
        flags = data[offset]
        length = int.from_bytes(data[offset + 1 : offset + 5], "big")
        message = json.loads(data[offset + 5 : offset + 5 + length] or b"{}")
        offset += 5 + length
        if flags & 0x02:
            assert "error" not in message, message
        else:
            messages.append(message)
    return messages


async def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Condition not met in time")
        await asyncio.sleep(0.01)
