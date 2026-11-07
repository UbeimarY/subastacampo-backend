import uuid
from datetime import UTC, datetime

import pytest
from connectrpc.errors import ConnectError

from subastacampo.pagination import (
    MAX_PAGE_SIZE,
    Cursor,
    decode_cursor,
    encode_cursor,
    resolve_page_size,
)


def test_cursor_round_trips_through_the_page_token() -> None:
    cursor = Cursor(created_at=datetime(2026, 11, 4, 15, 30, tzinfo=UTC), id=uuid.uuid4())

    assert decode_cursor(encode_cursor(cursor)) == cursor


def test_empty_token_means_first_page() -> None:
    assert decode_cursor("") is None


def test_tampered_token_is_rejected() -> None:
    with pytest.raises(ConnectError):
        decode_cursor("not-a-valid-token")


def test_page_size_has_a_default_and_a_ceiling() -> None:
    assert resolve_page_size(0) == 20
    assert resolve_page_size(500) == MAX_PAGE_SIZE
