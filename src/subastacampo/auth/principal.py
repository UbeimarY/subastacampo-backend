"""Identity of the caller for the current request.

The authentication middleware stores it in a context variable. Every coroutine
of the same request (including a long-running stream) sees the same value.
"""

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

from connectrpc.code import Code

from subastacampo.db.models import UserRole
from subastacampo.errors import rpc_error
from subastacampo.v1 import common_pb2


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    role: UserRole


@dataclass(frozen=True, slots=True)
class Authentication:
    principal: Principal | None = None
    # A token was sent, but it was expired, tampered with or malformed.
    token_rejected: bool = False


ANONYMOUS = Authentication()

_current: ContextVar[Authentication] = ContextVar("authentication", default=ANONYMOUS)

_ROLE_REASONS = {
    UserRole.PRODUCER: common_pb2.ERROR_REASON_PRODUCER_ONLY,
    UserRole.BUYER: common_pb2.ERROR_REASON_BUYER_ONLY,
}


def set_authentication(authentication: Authentication) -> Token[Authentication]:
    return _current.set(authentication)


def reset_authentication(token: Token[Authentication]) -> None:
    _current.reset(token)


def optional_principal() -> Principal | None:
    return _current.get().principal


def require_principal(*, role: UserRole | None = None) -> Principal:
    """Returns the caller, or raises the right error if they are not allowed."""
    authentication = _current.get()
    principal = authentication.principal

    if principal is None:
        if authentication.token_rejected:
            raise rpc_error(
                Code.UNAUTHENTICATED,
                common_pb2.ERROR_REASON_SESSION_EXPIRED,
                "Access token is expired or invalid",
            )
        raise rpc_error(
            Code.UNAUTHENTICATED,
            common_pb2.ERROR_REASON_AUTHENTICATION_REQUIRED,
            "This RPC requires an access token",
        )

    if role is not None and principal.role != role:
        raise rpc_error(
            Code.PERMISSION_DENIED,
            _ROLE_REASONS[role],
            f"This RPC is only available to the {role.value} role",
        )
    return principal
