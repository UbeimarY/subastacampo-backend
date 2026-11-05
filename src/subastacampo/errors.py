"""Contract-defined errors: a Connect code plus a machine-readable reason.

Messages are for developers (logs, debugging). Clients show the user a text
translated from the reason, never the message.
"""

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.errors import ErrorDetail as ConnectErrorDetail
from google.protobuf.message import Message as GoogleMessage
from protobuf.wkt import Any

from subastacampo.v1 import common_pb2
from subastacampo.v1.common_pb2 import ErrorDetail, Money

_TYPE_URL_PREFIX = "type.googleapis.com/"


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
    return ConnectError(code, message, details=[pack_detail(detail)])


def validation_error(field: str, message: str) -> ConnectError:
    return rpc_error(
        Code.INVALID_ARGUMENT,
        common_pb2.ERROR_REASON_VALIDATION_FAILED,
        message,
        field=field,
    )


def pack_detail(message: GoogleMessage) -> ConnectErrorDetail:
    """Wraps a message generated for Google's protobuf runtime as a Connect error detail.

    connectrpc 0.13 packs details with protobuf-py, which cannot read messages
    generated for Google's runtime. An Any is just a type URL plus the serialized
    bytes, and the binary wire format is identical in every runtime, so building
    it here is runtime-independent.
    """
    return ConnectErrorDetail(
        Any(
            type_url=_TYPE_URL_PREFIX + message.DESCRIPTOR.full_name,
            value=message.SerializeToString(),
        )
    )
