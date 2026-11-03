"""Routes each request to the Connect service named in its URL path."""

from collections.abc import Awaitable, Callable, Mapping, Sequence

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LifespanHook = Callable[[], Awaitable[None]]

HEALTH_PATH = "/healthz"


class ConnectRouter:
    """Dispatches /<package>.<Service>/<Method> to the matching Connect application.

    Connect applications expect the original path, so the scope is passed through
    untouched (a Starlette Mount would rewrite it).
    """

    def __init__(
        self,
        services: Mapping[str, ASGIApp],
        *,
        on_startup: Sequence[LifespanHook] = (),
        on_shutdown: Sequence[LifespanHook] = (),
    ) -> None:
        self._services = dict(services)
        self._on_startup = list(on_startup)
        self._on_shutdown = list(on_shutdown)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
            return
        if scope["type"] != "http":
            return

        path: str = scope["path"]
        if path == HEALTH_PATH:
            await PlainTextResponse("ok")(scope, receive, send)
            return

        service_name = path.removeprefix("/").split("/", 1)[0]
        service = self._services.get(service_name)
        if service is None:
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        await service(scope, receive, send)

    async def _handle_lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                for hook in self._on_startup:
                    await hook()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                for hook in self._on_shutdown:
                    await hook()
                await send({"type": "lifespan.shutdown.complete"})
                return

            