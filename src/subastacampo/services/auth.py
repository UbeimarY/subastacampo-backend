"""Account registration and authentication."""

import re

from connectrpc.code import Code
from connectrpc.request import RequestContext
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from subastacampo.auth.passwords import hash_password, verify_password
from subastacampo.auth.principal import Principal, require_principal
from subastacampo.auth.tokens import TokenService
from subastacampo.db.models import User
from subastacampo.errors import rpc_error, validation_error
from subastacampo.mappers import ROLE_FROM_PROTO, to_timestamp, to_user_message
from subastacampo.v1 import common_pb2
from subastacampo.v1.auth_connect import AuthService
from subastacampo.v1.auth_pb2 import (
    GetCurrentUserRequest,
    GetCurrentUserResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)

PASSWORD_MIN_LENGTH = 8
# Argon2 cost grows with the input size; this also stops absurd payloads.
PASSWORD_MAX_LENGTH = 128
_PHONE_PATTERN = re.compile(r"^\+?[0-9 ]{7,20}$")


class AuthServiceImpl(AuthService):
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], tokens: TokenService
    ) -> None:
        self._session_factory = session_factory
        self._tokens = tokens

    async def register(
        self,
        request: RegisterRequest,
        ctx: RequestContext[RegisterRequest, RegisterResponse],
    ) -> RegisterResponse:
        full_name = request.full_name.strip()
        if not 2 <= len(full_name) <= 100:
            raise validation_error("full_name", "Full name must have 2 to 100 characters")

        email = _normalize_email(request.email)

        if not PASSWORD_MIN_LENGTH <= len(request.password) <= PASSWORD_MAX_LENGTH:
            raise validation_error("password", "Password must have 8 to 128 characters")

        role = ROLE_FROM_PROTO.get(request.role)
        if role is None:
            raise validation_error("role", "Role must be producer or buyer")

        phone = _optional_text(request, "phone")
        if phone is not None and not _PHONE_PATTERN.fullmatch(phone):
            raise validation_error("phone", "Phone must have 7 to 20 digits")

        municipality = _optional_text(request, "municipality")
        if municipality is not None and len(municipality) > 100:
            raise validation_error("municipality", "Municipality must have at most 100 characters")

        user = User(
            full_name=full_name,
            email=email,
            password_hash=await hash_password(request.password),
            role=role,
            phone=phone,
            municipality=municipality,
        )

        async with self._session_factory() as session:
            session.add(user)
            try:
                await session.commit()
            except IntegrityError as error:
                # The UNIQUE constraint also covers two simultaneous registrations.
                raise rpc_error(
                    Code.ALREADY_EXISTS,
                    common_pb2.ERROR_REASON_EMAIL_ALREADY_REGISTERED,
                    "Email is already registered",
                    field="email",
                ) from error
            await session.refresh(user)

        return RegisterResponse(user=to_user_message(user))

    async def login(
        self,
        request: LoginRequest,
        ctx: RequestContext[LoginRequest, LoginResponse],
    ) -> LoginResponse:
        email = request.email.strip().lower()
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))

        # Always verify a hash (a dummy one if the user does not exist), so the
        # response time does not reveal which emails are registered.
        password = request.password[:PASSWORD_MAX_LENGTH]
        password_ok = await verify_password(password, user.password_hash if user else None)
        if user is None or not password_ok:
            raise rpc_error(
                Code.UNAUTHENTICATED,
                common_pb2.ERROR_REASON_INVALID_CREDENTIALS,
                "Invalid email or password",
            )

        issued = self._tokens.issue(Principal(user_id=user.id, role=user.role))
        return LoginResponse(
            access_token=issued.value,
            expires_at=to_timestamp(issued.expires_at),
            user=to_user_message(user),
        )

    async def get_current_user(
        self,
        request: GetCurrentUserRequest,
        ctx: RequestContext[GetCurrentUserRequest, GetCurrentUserResponse],
    ) -> GetCurrentUserResponse:
        principal = require_principal()
        async with self._session_factory() as session:
            user = await session.get(User, principal.user_id)
        if user is None:
            raise rpc_error(
                Code.UNAUTHENTICATED,
                common_pb2.ERROR_REASON_SESSION_EXPIRED,
                "The account no longer exists",
            )
        return GetCurrentUserResponse(user=to_user_message(user))


def _normalize_email(raw: str) -> str:
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as error:
        raise validation_error("email", "Email address is not valid") from error


def _optional_text(request: RegisterRequest, field: str) -> str | None:
    """Returns the trimmed value of an optional field, or None if absent or blank."""
    if not request.HasField(field):
        return None
    value = getattr(request, field).strip()
    return value or None
