"""ASGI application factory: wires settings, database and Connect services."""

import tomllib
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from subastacampo.auctions.broker import AuctionEventBroker
from subastacampo.auctions.closer import AuctionCloser
from subastacampo.auth.middleware import AuthenticationMiddleware
from subastacampo.auth.tokens import TokenService
from subastacampo.calendar import BusinessCalendar
from subastacampo.clock import Clock, SystemClock
from subastacampo.config import Settings, get_settings
from subastacampo.pricing.model import PricingModel
from subastacampo.routing import ConnectRouter
from subastacampo.services.auctions import AuctionServiceImpl
from subastacampo.services.auth import AuthServiceImpl
from subastacampo.services.catalog import CatalogServiceImpl
from subastacampo.services.system import SystemServiceImpl
from subastacampo.v1 import auction_pb2, auth_pb2, catalog_pb2, system_pb2
from subastacampo.v1.auction_connect import AuctionServiceASGIApplication
from subastacampo.v1.auth_connect import AuthServiceASGIApplication
from subastacampo.v1.catalog_connect import CatalogServiceASGIApplication
from subastacampo.v1.system_connect import SystemServiceASGIApplication

# Headers the browser client sends; CORS must allow them explicitly.
CONNECT_REQUEST_HEADERS = [
    "authorization",
    "content-type",
    "connect-protocol-version",
    "connect-timeout-ms",
    "x-user-agent",
]

# src/subastacampo/app.py -> repository root, where pyproject.toml lives.
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def create_app(
    settings: Settings | None = None,
    *,
    calendar: BusinessCalendar | None = None,
    clock: Clock | None = None,
    broker: AuctionEventBroker | None = None,
    closer_interval_seconds: float = 1.0,
) -> ASGIApp:
    settings = settings or get_settings()
    calendar = calendar or BusinessCalendar(settings.business_timezone)
    clock = clock or SystemClock()
    broker = broker or AuctionEventBroker()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tokens = TokenService(settings.jwt_secret, timedelta(minutes=settings.jwt_expire_minutes))
    # Fail fast: without a valid model the server must not start.
    pricing = PricingModel.load()
    app_version = _app_version()

    services: dict[str, ASGIApp] = {
        _service_name(system_pb2, "SystemService"): SystemServiceASGIApplication(
            SystemServiceImpl(session_factory, app_version)
        ),
        _service_name(auth_pb2, "AuthService"): AuthServiceASGIApplication(
            AuthServiceImpl(session_factory, tokens)
        ),
        _service_name(catalog_pb2, "CatalogService"): CatalogServiceASGIApplication(
            CatalogServiceImpl(session_factory, calendar, pricing)
        ),
        _service_name(auction_pb2, "AuctionService"): AuctionServiceASGIApplication(
            AuctionServiceImpl(session_factory, calendar, clock, broker)
        ),
    }

    closer = AuctionCloser(session_factory, broker, clock, calendar, closer_interval_seconds)
    router = ConnectRouter(
        services,
        on_startup=[closer.start],
        # Stop the background task before closing the database connections.
        on_shutdown=[closer.stop, engine.dispose],
    )

    # Outermost first: CORS answers preflights, then the caller is identified.
    return CORSMiddleware(
        AuthenticationMiddleware(router, tokens),
        allow_origins=settings.cors_origins,
        # POST for every RPC, GET for side-effect-free RPCs (cacheable)
        allow_methods=["GET", "POST"],
        allow_headers=CONNECT_REQUEST_HEADERS,
        max_age=3600,
    )


def _service_name(module: ModuleType, service: str) -> str:
    """Fully qualified name taken from the contract itself, never hard-coded."""
    return module.DESCRIPTOR.services_by_name[service].full_name


def _app_version() -> str:
    """Installed package version; from a source checkout, read from pyproject.toml.

    Either way the version has a single source of truth: pyproject.toml.
    """
    try:
        return version("subastacampo")
    except PackageNotFoundError:
        with _PYPROJECT.open("rb") as file:
            return str(tomllib.load(file)["project"]["version"])
