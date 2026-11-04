"""Conversions between database models and contract messages."""

from datetime import datetime

from google.protobuf.timestamp_pb2 import Timestamp

from subastacampo.db.models import User, UserRole
from subastacampo.v1 import common_pb2
from subastacampo.v1.auth_pb2 import User as UserMessage

_ROLE_TO_PROTO = {
    UserRole.PRODUCER: common_pb2.USER_ROLE_PRODUCER,
    UserRole.BUYER: common_pb2.USER_ROLE_BUYER,
}
ROLE_FROM_PROTO = {proto: role for role, proto in _ROLE_TO_PROTO.items()}


def to_timestamp(value: datetime) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp


def to_user_message(user: User) -> UserMessage:
    message = UserMessage(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        role=_ROLE_TO_PROTO[user.role],
        created_at=to_timestamp(user.created_at),
    )
    if user.phone is not None:
        message.phone = user.phone
    if user.municipality is not None:
        message.municipality = user.municipality
    return message
