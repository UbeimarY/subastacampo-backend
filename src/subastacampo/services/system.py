"""Operational status of the service."""

import logging

from connectrpc.request import RequestContext
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from subastacampo.v1.system_connect import SystemService
from subastacampo.v1.system_pb2 import GetStatusRequest, GetStatusResponse

logger = logging.getLogger(__name__)


class SystemServiceImpl(SystemService):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], version: str) -> None:
        self._session_factory = session_factory
        self._version = version

    async def get_status(
        self,
        request: GetStatusRequest,
        ctx: RequestContext[GetStatusRequest, GetStatusResponse],
    ) -> GetStatusResponse:
        response = GetStatusResponse(version=self._version, database_ok=await self._database_ok())
        response.server_time.GetCurrentTime()
        return response

    async def _database_ok(self) -> bool:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            logger.exception("Database health check failed")
            return False
        return True

    