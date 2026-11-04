"""Signed access tokens (JWT, HS256)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from subastacampo.auth.principal import Principal
from subastacampo.db.models import UserRole

ISSUER = "subastacampo"
AUDIENCE = "subastacampo-web"
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32


class InvalidTokenError(Exception):
    """The token is expired, tampered with or malformed."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    value: str
    expires_at: datetime


class TokenService:
    def __init__(self, secret: str, lifetime: timedelta) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"JWT secret must have at least {MIN_SECRET_LENGTH} characters")
        self._secret = secret
        self._lifetime = lifetime

    def issue(self, principal: Principal, *, now: datetime | None = None) -> IssuedToken:
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self._lifetime
        claims = {
            "sub": str(principal.user_id),
            "role": principal.role.value,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": issued_at,
            "exp": expires_at,
        }
        return IssuedToken(jwt.encode(claims, self._secret, algorithm=ALGORITHM), expires_at)

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub"]},
            )
            return Principal(user_id=uuid.UUID(claims["sub"]), role=UserRole(claims["role"]))
        except (jwt.PyJWTError, KeyError, ValueError) as error:
            raise InvalidTokenError(str(error)) from error

        