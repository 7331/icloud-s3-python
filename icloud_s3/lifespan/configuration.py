from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from icloud_s3.carrier.service import DEFAULT_CHUNK_SIZE_BYTES, MAXIMUM_CHUNK_SIZE_BYTES


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ICLOUD_S3_", env_file=".env", frozen=True, extra="ignore")

    host: str = "127.0.0.1"
    port: int = 9311
    album_directory: Path | None = None
    shard_count: int = Field(default=1, ge=1, le=200)
    maximum_chunk_size_bytes: int = Field(default=DEFAULT_CHUNK_SIZE_BYTES, ge=64, le=MAXIMUM_CHUNK_SIZE_BYTES)
    is_photos_app_sync_enabled: bool = False
