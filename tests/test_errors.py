from connectrpc.code import Code

from subastacampo.errors import rpc_error
from subastacampo.v1 import common_pb2


def test_error_detail_is_named_after_the_contract_message() -> None:
    error = rpc_error(Code.INVALID_ARGUMENT, common_pb2.ERROR_REASON_VALIDATION_FAILED, "bad")

    assert error.details[0].type_name == "subastacampo.v1.ErrorDetail"


def test_error_detail_round_trips_through_the_wire_format() -> None:
    error = rpc_error(
        Code.INVALID_ARGUMENT,
        common_pb2.ERROR_REASON_VALIDATION_FAILED,
        "bad",
        field="email",
    )

    decoded = common_pb2.ErrorDetail.FromString(error.details[0].message_bytes)

    assert decoded.reason == common_pb2.ERROR_REASON_VALIDATION_FAILED
    assert decoded.field == "email"
