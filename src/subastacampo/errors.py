"""Contract-defined errors: a Connect code plus a machine-readable reason.

Messages are for developers (logs, debugging). Clients show the user a text
translated from the reason, never the message.
"""

from connectrpc.code import Code
from connectrpc.errors import ConnectError

from subastacampo.v1 import common_pb2
from subastacampo.v1.common_pb2 import ErrorDetail, Money


def rpc_error(
    code: Code,
    reason: int,
    message: str,
    *,
    field: str = "",
    minimum_bid: Money | None = None,
) -> ConnectError:
    detail = ErrorDetail(reason=reason, field=field)
    if minimum_bid is not None:
        detail.minimum_bid.CopyFrom(minimum_bid)
    return ConnectError(code, message, details=[detail])


def validation_error(field: str, message: str) -> ConnectError:
    return rpc_error(
        Code.INVALID_ARGUMENT,
        common_pb2.ERROR_REASON_VALIDATION_FAILED,
        message,
        field=field,
    )

    