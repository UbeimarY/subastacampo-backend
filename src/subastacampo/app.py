"""ASGI application factory: wires settings, database and Connect services."""

from importlib.metadata import version
from types import ModuleType

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from subastacampo.config import Settings, get_settings
from subastacampo.routing import ConnectRouter
from subastacampo.services.system import SystemServiceImpl
from subastacampo.v1 import system_pb2
from subastacampo.v1.system_connect import SystemServiceASGIApplication

# Headers the browser client sends; CORS must allow them explicitly.
CONNECT_REQUEST_HEADERS = [
    "authorization",
    "content-type",
    "connect-protocol-version",
    "connect-timeout-ms",
    "x-user-agent",
]


def create_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or get_settings()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app_version = version("subastacampo")

    services: dict[str, ASGIApp] = {
        _service_name(system_pb2, "SystemService"): SystemServiceASGIApplication(
            SystemServiceImpl(session_factory, app_version)
        ),
    }

    router = ConnectRouter(services, on_shutdown=[engine.dispose])

    return CORSMiddleware(
        router,
        allow_origins=settings.cors_origins,
        # POST for every RPC, GET for side-effect-free RPCs (cacheable)
        allow_methods=["GET", "POST"],
        allow_headers=CONNECT_REQUEST_HEADERS,
        max_age=3600,
    )


def _service_name(module: ModuleType, service: str) -> str:
    """Fully qualified name taken from the contract itself, never hard-coded."""
    return module.DESCRIPTOR.services_by_name[service].full_name

