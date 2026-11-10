# SubastaCampo · Backend

[![CI](https://github.com/UbeimarY/subastacampo-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/UbeimarY/subastacampo-backend/actions/workflows/ci.yml)

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
| Tooling | uv, Ruff, pytest, GitHub Actions |

## What it does

- **Live auctions** streamed to every participant (`WatchAuction`), with anti-sniping: a bid
  in the last two minutes pushes the close.
- **Automatic bidding** (`SetAutoBid`): a buyer sets a private maximum and the server bids for
  them only what it takes to lead. The exchange between several maxima is decided by a pure
  function (`auctions/proxy.py`) inside the auction's row lock: the highest maximum leads one
  increment above the runner-up's, the earliest wins ties, nobody bids above their maximum.
- **Price suggestion** (`SuggestPrice`), explained with plain factors and **recent comparable
  sales** of the same category.
- **Market prices** (`GetMarketPrices`, public): typical price per kilo by category and week.
- **Risk score** for every bid (last-second bids, new accounts, shared phone with the producer),
  shown only to the producer.

## Price model (`price-v2`)

Gradient-boosted quantile regression (10th, 50th and 90th percentiles) with monotonic
constraints (fresher, more demand: never cheaper; bigger lot: never more per kilo).

The range is **calibrated by conformalized quantile regression**: on a split the models never
saw, it measures how far prices fall outside the range, relative to the median, and widens it
just enough to keep its promise.

| Held-out test split (synthetic data) | Before | **After calibration** |
|---|---|---|
| Prices inside the "80 %" range | 71.3 % | **80.7 %** |
| Every category | — | **79.6–82.4 %** |
| Mean range width, relative to the price | 19.1 % | 23.3 % |

Median error: $171 per kilo against $477 for the category average (64 % better). The data is
synthetic until real sales exist; see `src/subastacampo/pricing/artifacts/model_card.json`.

## Demo data

```bash
DEMO_PASSWORD='choose-one' uv run python -m subastacampo.demo
```

Creates producers and buyers from Nariño, six weeks of closed auctions and eight live ones
closing over the next hours (one with an automatic bid waiting). It runs only once.

## Related repositories

- Frontend: `subastacampo-frontend` (Angular)
- Previous version: `subastacampo-v1` (REST + WebSockets, archived)

## Project status

🚧 In development. Course project for *Web-Oriented Programming*.