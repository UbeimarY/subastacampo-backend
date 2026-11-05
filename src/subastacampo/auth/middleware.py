"""Resolves the caller's identity before Connect reads the request body."""

from starlette.types import ASGIApp, Receive, Scope, Send

from subastacampo.auth.principal import (
    ANONYMOUS,
    Authentication,
    reset_authentication,
    set_authentication,
)
from subastacampo.auth.tokens import InvalidTokenError, TokenService

_BEARER_PREFIX = "bearer "


class AuthenticationMiddleware:
    """Only identifies the caller; each RPC decides what it requires.

    Rejections are raised inside the handlers, so Connect encodes them
    correctly for every protocol (Connect and gRPC).
    """

    def __init__(self, app: ASGIApp, tokens: TokenService) -> None:
        self._app = app
        self._tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        context_token = set_authentication(self._authenticate(scope))
        try:
            await self._app(scope, receive, send)
        finally:
            reset_authentication(context_token)

    def _authenticate(self, scope: Scope) -> Authentication:
        header = _header(scope, b"authorization")
        if header is None or not header.lower().startswith(_BEARER_PREFIX):
            return ANONYMOUS
        try:
            principal = self._tokens.verify(header[len(_BEARER_PREFIX) :].strip())
        except InvalidTokenError:
            return Authentication(token_rejected=True)
        return Authentication(principal=principal)


def _header(scope: Scope, name: bytes) -> str | None:
    """ASGI header names are lowercase bytes."""
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return None
