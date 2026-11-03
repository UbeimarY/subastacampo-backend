# SubastaCampo · Backend

Real-time auction API for small farmers, built **contract-first** with Protocol Buffers.
Bids are streamed live to every participant over server streaming RPCs.

The same service speaks two protocols from a single `.proto` contract:

- **Connect protocol** (HTTP/1.1 and HTTP/2): used by the browser, no proxy needed.
- **gRPC protocol** (HTTP/2): used by native gRPC clients.

## Tech stack

| Concern | Technology |
|---|---|
| Contract | Protocol Buffers + Buf |
| RPC framework | Connect RPC for Python (ASGI) |
| Server | Uvicorn |
| Database | PostgreSQL, SQLAlchemy 2, Alembic |
| AI | scikit-learn (price suggestion) + risk scoring for suspicious bids |
| Tooling | uv, Ruff, pytest |

## Related repositories

- Frontend: `subastacampo-frontend` (Angular)
- Previous version: `subastacampo-v1` (REST + WebSockets, archived)

## Project status

🚧 In development. Course project for *Web-Oriented Programming*.