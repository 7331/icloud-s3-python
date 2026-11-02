from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class FrameHeader(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    version: int
    bucket: str
    key: str
    object_id: str
    content_type: str
    total_size: int
    chunk_index: int
    total_chunks: int
    chunk_size: int
    etag: str
    object_sha256: str
    created_at: AwareDatetime
    metadata: dict[str, str] = Field(default_factory=dict[str, str])


class ObjectDescription(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    bucket: str
    key: str
    content_type: str
    metadata: dict[str, str]


class EncodedFrame(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    header: FrameHeader
    png_bytes: bytes


class DecodedFrame(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    header: FrameHeader
    chunk: bytes


class DecodedObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    header: FrameHeader
    data: bytes
