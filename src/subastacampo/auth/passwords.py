"""Password hashing with Argon2id.

Hashing is deliberately slow (to resist brute force), so it runs in a worker
thread: the event loop stays free to serve live auction streams.
"""

import asyncio

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()

# Used when the email does not exist, so a failed login takes the same time
# whether or not the account exists (prevents user enumeration).
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-login")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    return await asyncio.to_thread(_hasher.verify, password, password_hash or _DUMMY_HASH)
