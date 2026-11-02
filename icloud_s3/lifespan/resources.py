from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os

from pydantic import BaseModel, ConfigDict

from icloud_s3.albums.photo_folder import PhotoFolder
from icloud_s3.albums.service import SharedAlbumService
from icloud_s3.infrastructure.macos_photos.client import MacosPhotosClient
from icloud_s3.lifespan.configuration import ServerSettings
from icloud_s3.shared.thread_lanes import ThreadLane

LANE_QUEUE_TIMEOUT_SECONDS = 30.0
ALBUM_FILE_LANE_THREADS = 4


class Resources(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    album_service: SharedAlbumService


@asynccontextmanager
async def open_resources(settings: ServerSettings) -> AsyncGenerator[Resources]:
    carrier_lane = ThreadLane(
        "carrier",
        threads=os.process_cpu_count() or 1,
        queue_timeout_seconds=LANE_QUEUE_TIMEOUT_SECONDS,
    )
    album_file_lane = ThreadLane(
        "album-files",
        threads=ALBUM_FILE_LANE_THREADS,
        queue_timeout_seconds=LANE_QUEUE_TIMEOUT_SECONDS,
    )
    try:
        album_service = SharedAlbumService(
            shard_count=settings.shard_count,
            maximum_chunk_size_bytes=settings.maximum_chunk_size_bytes,
            photo_folder=None if settings.album_directory is None else PhotoFolder(settings.album_directory),
            photos_client=MacosPhotosClient() if settings.is_photos_app_sync_enabled else None,
            carrier_lane=carrier_lane,
            album_file_lane=album_file_lane,
        )
        await album_service.reload_from_photo_folder()
        yield Resources(album_service=album_service)
    finally:
        carrier_lane.shut_down()
        album_file_lane.shut_down()
