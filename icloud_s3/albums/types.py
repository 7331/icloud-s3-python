from pydantic import AwareDatetime, BaseModel, ConfigDict

from icloud_s3.albums.errors import InvalidRangeError
from icloud_s3.carrier.types import FrameHeader


class ObjectLocation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    bucket_name: str
    key: str


class StoredObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    header: FrameHeader
    album_name: str
    photo_ids: tuple[str, ...]


class ObjectContent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    stored_object: StoredObject
    data: bytes


class CarrierPhoto(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    stored_object: StoredObject
    png_bytes: bytes


class BucketSummary(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    name: str
    created_at: AwareDatetime


class ObjectListingPage(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    contents: tuple[StoredObject, ...]
    common_prefixes: tuple[str, ...]
    last_listed_key: str
    is_truncated: bool


class PhotoFile(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    album_name: str
    photo_id: str
    png_bytes: bytes


class ByteRange(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    first_byte: int
    last_byte: int

    def slice_bytes(self, data: bytes) -> bytes:
        return data[self.first_byte : self.last_byte + 1]


class RequestedByteRange(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    first_byte: int | None
    last_byte: int | None

    def resolve(self, *, total_size_bytes: int) -> ByteRange:
        if self.first_byte is not None:
            first_byte = self.first_byte
            last_byte = total_size_bytes - 1 if self.last_byte is None else min(self.last_byte, total_size_bytes - 1)
        else:
            first_byte = max(total_size_bytes - (self.last_byte or 0), 0)
            last_byte = total_size_bytes - 1
        if first_byte > last_byte:
            message = f"bytes={self.first_byte}-{self.last_byte} is not satisfiable for {total_size_bytes} bytes"
            raise InvalidRangeError(message)
        return ByteRange(first_byte=first_byte, last_byte=last_byte)
