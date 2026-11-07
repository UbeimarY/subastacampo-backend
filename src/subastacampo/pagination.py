"""Keyset (cursor) pagination with opaque page tokens.

Unlike OFFSET pagination, pages stay consistent while new rows are inserted,
and the database seeks straight to the next page through an index.
"""

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from subastacampo.errors import validation_error

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position of the last row of a page: (created_at, id) is unique and ordered."""

    created_at: datetime
    id: uuid.UUID


def resolve_page_size(requested: int) -> int:
    if requested < 0:
        raise validation_error("page_size", "Page size cannot be negative")
    return min(requested or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)


def encode_cursor(cursor: Cursor) -> str:
    raw = json.dumps({"c": cursor.created_at.isoformat(), "i": str(cursor.id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> Cursor | None:
    if not token:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode()))
        return Cursor(created_at=datetime.fromisoformat(data["c"]), id=uuid.UUID(data["i"]))
    except (ValueError, KeyError, TypeError) as error:
        raise validation_error("page_token", "Page token is invalid") from error
