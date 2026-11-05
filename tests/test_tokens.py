import uuid
from datetime import UTC, datetime, timedelta

import pytest

from subastacampo.auth.principal import Principal
from subastacampo.auth.tokens import InvalidTokenError, TokenService
from subastacampo.db.models import UserRole
from tests.conftest import TEST_JWT_SECRET

PRINCIPAL = Principal(user_id=uuid.uuid4(), role=UserRole.BUYER)


def test_issued_token_identifies_the_user() -> None:
    tokens = TokenService(TEST_JWT_SECRET, timedelta(hours=1))

    assert tokens.verify(tokens.issue(PRINCIPAL).value) == PRINCIPAL


def test_expired_token_is_rejected() -> None:
    tokens = TokenService(TEST_JWT_SECRET, timedelta(hours=1))
    issued = tokens.issue(PRINCIPAL, now=datetime.now(UTC) - timedelta(hours=2))

    with pytest.raises(InvalidTokenError):
        tokens.verify(issued.value)


def test_token_signed_with_another_secret_is_rejected() -> None:
    attacker = TokenService("another-secret-that-is-also-long-enough", timedelta(hours=1))
    tokens = TokenService(TEST_JWT_SECRET, timedelta(hours=1))

    with pytest.raises(InvalidTokenError):
        tokens.verify(attacker.issue(PRINCIPAL).value)


def test_short_secrets_are_refused() -> None:
    with pytest.raises(ValueError):
        TokenService("too-short", timedelta(hours=1))
