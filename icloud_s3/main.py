from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import logging

from fastapi import FastAPI
import uvicorn

from icloud_s3.albums.router import router as albums_router
from icloud_s3.lifespan.configuration import ServerSettings
from icloud_s3.lifespan.resources import open_resources
from icloud_s3.shared.exception_handlers import register_exception_handlers

logger = logging.getLogger("uvicorn.error")


def build_lifespan(settings: ServerSettings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        async with open_resources(settings) as resources:
            application.state.album_service = resources.album_service
            logger.info(
                "icloud-s3 on %s: %d buckets, %d objects, 0 B of iCloud quota used",
                settings.album_directory or "an in-memory Shared Album",
                len(resources.album_service.buckets),
                resources.album_service.count_objects(),
            )
            yield

    return lifespan


def create_application(settings: ServerSettings) -> FastAPI:
    application = FastAPI(
        lifespan=build_lifespan(settings),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
    )
    register_exception_handlers(application)
    application.include_router(albums_router)
    return application


def main() -> None:
    settings = ServerSettings()
    uvicorn.run(create_application(settings), host=settings.host, port=settings.port, loop="uvloop")
