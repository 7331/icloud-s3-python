import asyncio
from datetime import UTC, datetime
import hashlib
import re
import uuid
import zlib

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from icloud_s3.albums.enums import MetadataDirective
from icloud_s3.albums.errors import (
    AlbumFullError,
    BucketNotEmptyError,
    InvalidBucketNameError,
    InvalidPartError,
    NoSuchBucketError,
    NoSuchKeyError,
    NoSuchUploadError,
    TooManyBucketsError,
)
from icloud_s3.albums.photo_folder import ALBUM_NAME_PREFIX, PhotoFolder
from icloud_s3.albums.types import (
    BucketSummary,
    CarrierPhoto,
    ObjectContent,
    ObjectListingPage,
    ObjectLocation,
    PhotoFile,
    RequestedByteRange,
    StoredObject,
)
from icloud_s3.carrier.errors import CarrierError
from icloud_s3.carrier.service import decode_frame, decode_object, encode_object
from icloud_s3.carrier.types import EncodedFrame, FrameHeader, ObjectDescription
from icloud_s3.infrastructure.macos_photos.client import MacosPhotosClient
from icloud_s3.shared.thread_lanes import ThreadLane

APPLE_MAXIMUM_SHARED_ALBUMS = 200
APPLE_MAXIMUM_PHOTOS_PER_ALBUM = 5000
BUCKET_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,63}")
UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")


class Bucket(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    created_at: AwareDatetime
    objects: dict[str, StoredObject] = Field(default_factory=dict[str, StoredObject])
    photos: dict[str, bytes] = Field(default_factory=dict[str, bytes])
    album_photo_counts: dict[str, int] = Field(default_factory=dict[str, int])


class MultipartUpload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    description: ObjectDescription
    parts: dict[int, bytes] = Field(default_factory=dict[int, bytes])


class DiscoveredFrame(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    photo_file: PhotoFile
    header: FrameHeader


def build_photo_id(header: FrameHeader) -> str:
    safe_key = UNSAFE_FILENAME_CHARACTERS.sub("_", header.key[:48]) or "object"
    return (
        f"{safe_key}_{header.etag[:8]}_chunk{header.chunk_index + 1:03}of{header.total_chunks:03}"
        f"_{header.object_id[:8]}"
    )


def discover_frames(photo_files: list[PhotoFile]) -> list[DiscoveredFrame]:
    discovered_frames: list[DiscoveredFrame] = []
    for photo_file in photo_files:
        try:
            header = decode_frame(photo_file.png_bytes).header
        except CarrierError:
            continue
        discovered_frames.append(DiscoveredFrame(photo_file=photo_file, header=header))
    return discovered_frames


def select_latest_complete_objects(discovered_frames: list[DiscoveredFrame]) -> list[list[DiscoveredFrame]]:
    frames_by_object_id: dict[str, list[DiscoveredFrame]] = {}
    for discovered_frame in discovered_frames:
        frames_by_object_id.setdefault(discovered_frame.header.object_id, []).append(discovered_frame)
    latest_frames_by_location: dict[tuple[str, str], list[DiscoveredFrame]] = {}
    for frames in sorted(frames_by_object_id.values(), key=lambda frames: frames[0].header.created_at):
        frames.sort(key=lambda frame: frame.header.chunk_index)
        first_header = frames[0].header
        if [frame.header.chunk_index for frame in frames] == list(range(first_header.total_chunks)):
            latest_frames_by_location[first_header.bucket, first_header.key] = frames
    return list(latest_frames_by_location.values())


def paginate_objects(
    stored_objects: list[StoredObject],
    *,
    prefix: str,
    delimiter: str,
    maximum_keys: int,
) -> ObjectListingPage:
    contents: list[StoredObject] = []
    common_prefixes: list[str] = []
    last_listed_key = ""
    for stored_object in stored_objects:
        remainder = stored_object.header.key.removeprefix(prefix)
        common_prefix = (
            prefix + remainder.split(delimiter, 1)[0] + delimiter if delimiter and delimiter in remainder else ""
        )
        if not common_prefix or common_prefixes[-1:] != [common_prefix]:
            if len(contents) + len(common_prefixes) == maximum_keys:
                return ObjectListingPage(
                    contents=tuple(contents),
                    common_prefixes=tuple(common_prefixes),
                    last_listed_key=last_listed_key,
                    is_truncated=True,
                )
            if common_prefix:
                common_prefixes.append(common_prefix)
            else:
                contents.append(stored_object)
        last_listed_key = stored_object.header.key
    return ObjectListingPage(
        contents=tuple(contents),
        common_prefixes=tuple(common_prefixes),
        last_listed_key=last_listed_key,
        is_truncated=False,
    )


class SharedAlbumService:
    def __init__(
        self,
        *,
        shard_count: int,
        maximum_chunk_size_bytes: int,
        photo_folder: PhotoFolder | None,
        photos_client: MacosPhotosClient | None,
        carrier_lane: ThreadLane,
        album_file_lane: ThreadLane,
    ) -> None:
        self.shard_count = min(max(shard_count, 1), APPLE_MAXIMUM_SHARED_ALBUMS)
        self.maximum_chunk_size_bytes = maximum_chunk_size_bytes
        self.photo_folder = photo_folder
        self.photos_client = photos_client
        self.carrier_lane = carrier_lane
        self.album_file_lane = album_file_lane
        self.buckets: dict[str, Bucket] = {}
        self.multipart_uploads: dict[str, MultipartUpload] = {}
        self.lock = asyncio.Lock()

    def list_album_names(self, bucket_name: str) -> list[str]:
        if self.shard_count == 1:
            return [f"{ALBUM_NAME_PREFIX}{bucket_name}"]
        return [f"{ALBUM_NAME_PREFIX}{bucket_name}-{index:02}" for index in range(1, self.shard_count + 1)]

    def get_album_name(self, bucket_name: str, *, key: str) -> str:
        return self.list_album_names(bucket_name)[zlib.crc32(key.encode()) % self.shard_count]

    def get_bucket(self, bucket_name: str) -> Bucket:
        bucket = self.buckets.get(bucket_name)
        if bucket is None:
            message = f"The specified bucket '{bucket_name}' does not exist"
            raise NoSuchBucketError(message)
        return bucket

    def get_stored_object(self, location: ObjectLocation) -> StoredObject:
        stored_object = self.get_bucket(location.bucket_name).objects.get(location.key)
        if stored_object is None:
            message = f"The specified key '{location.key}' does not exist"
            raise NoSuchKeyError(message)
        return stored_object

    def get_multipart_upload(self, upload_id: str, *, location: ObjectLocation) -> MultipartUpload:
        upload = self.multipart_uploads.get(upload_id)
        if upload is None or (upload.description.bucket, upload.description.key) != (
            location.bucket_name,
            location.key,
        ):
            message = f"The specified multipart upload '{upload_id}' does not exist"
            raise NoSuchUploadError(message)
        return upload

    def count_objects(self) -> int:
        return sum(len(bucket.objects) for bucket in self.buckets.values())

    async def create_bucket(self, bucket_name: str) -> None:
        if not BUCKET_NAME_PATTERN.fullmatch(bucket_name):
            message = f"'{bucket_name}' is not a valid bucket name"
            raise InvalidBucketNameError(message)
        async with self.lock:
            if bucket_name in self.buckets:
                return
            if (len(self.buckets) + 1) * self.shard_count > APPLE_MAXIMUM_SHARED_ALBUMS:
                message = f"Apple allows at most {APPLE_MAXIMUM_SHARED_ALBUMS} Shared Albums per account"
                raise TooManyBucketsError(message)
            self.buckets[bucket_name] = Bucket(created_at=datetime.now(UTC))

    async def get_bucket_summary(self, bucket_name: str) -> BucketSummary:
        async with self.lock:
            return BucketSummary(name=bucket_name, created_at=self.get_bucket(bucket_name).created_at)

    async def list_buckets(self) -> list[BucketSummary]:
        async with self.lock:
            return [
                BucketSummary(name=bucket_name, created_at=bucket.created_at)
                for bucket_name, bucket in sorted(self.buckets.items())
            ]

    async def delete_bucket(self, bucket_name: str) -> None:
        async with self.lock:
            if self.get_bucket(bucket_name).objects:
                message = f"The bucket '{bucket_name}' you tried to delete is not empty"
                raise BucketNotEmptyError(message)
            del self.buckets[bucket_name]
            if self.photo_folder is not None:
                await self.album_file_lane.run(self.photo_folder.delete_albums, self.list_album_names(bucket_name))

    async def put_object(self, data: bytes, *, description: ObjectDescription) -> StoredObject:
        async with self.lock:
            self.get_bucket(description.bucket)
        frames = await self.carrier_lane.run(
            encode_object,
            data,
            description=description,
            maximum_chunk_size_bytes=self.maximum_chunk_size_bytes,
        )
        album_name = self.get_album_name(description.bucket, key=description.key)
        async with self.lock:
            bucket = self.get_bucket(description.bucket)
            previous_object = bucket.objects.get(description.key)
            previous_photo_count = len(previous_object.photo_ids) if previous_object else 0
            album_photo_count = bucket.album_photo_counts.get(album_name, 0) - previous_photo_count + len(frames)
            if album_photo_count > APPLE_MAXIMUM_PHOTOS_PER_ALBUM:
                message = f"Shared Album '{album_name}' reached Apple's {APPLE_MAXIMUM_PHOTOS_PER_ALBUM}-photo limit"
                raise AlbumFullError(message)
            if previous_object is not None:
                await self.remove_photos(bucket, stored_object=previous_object)
            stored_object = StoredObject(
                header=frames[0].header,
                album_name=album_name,
                photo_ids=tuple(build_photo_id(frame.header) for frame in frames),
            )
            await self.add_photos(bucket, stored_object=stored_object, frames=frames)
            bucket.objects[description.key] = stored_object
            return stored_object

    async def add_photos(self, bucket: Bucket, *, stored_object: StoredObject, frames: list[EncodedFrame]) -> None:
        for photo_id, frame in zip(stored_object.photo_ids, frames, strict=True):
            bucket.photos[photo_id] = frame.png_bytes
            if self.photo_folder is None:
                continue
            png_path = await self.album_file_lane.run(
                self.photo_folder.write_photo,
                frame.png_bytes,
                album_name=stored_object.album_name,
                photo_id=photo_id,
            )
            if self.photos_client is not None:
                await self.album_file_lane.run(
                    self.photos_client.import_png, png_path, album_name=stored_object.album_name
                )
        bucket.album_photo_counts[stored_object.album_name] = bucket.album_photo_counts.get(
            stored_object.album_name,
            0,
        ) + len(frames)

    async def remove_photos(self, bucket: Bucket, *, stored_object: StoredObject) -> None:
        for photo_id in stored_object.photo_ids:
            del bucket.photos[photo_id]
        bucket.album_photo_counts[stored_object.album_name] -= len(stored_object.photo_ids)
        if self.photo_folder is not None:
            await self.album_file_lane.run(
                self.photo_folder.delete_photos,
                stored_object.photo_ids,
                album_name=stored_object.album_name,
            )

    async def get_object_content(self, location: ObjectLocation) -> ObjectContent:
        async with self.lock:
            stored_object = self.get_stored_object(location)
            bucket = self.get_bucket(location.bucket_name)
            png_frames = [bucket.photos[photo_id] for photo_id in stored_object.photo_ids]
        decoded_object = await self.carrier_lane.run(decode_object, png_frames)
        return ObjectContent(stored_object=stored_object, data=decoded_object.data)

    async def get_carrier_photo(self, location: ObjectLocation, *, chunk_index: int) -> CarrierPhoto:
        async with self.lock:
            stored_object = self.get_stored_object(location)
            photo_ids = stored_object.photo_ids
            photo_id = photo_ids[chunk_index if 0 <= chunk_index < len(photo_ids) else 0]
            return CarrierPhoto(
                stored_object=stored_object,
                png_bytes=self.get_bucket(location.bucket_name).photos[photo_id],
            )

    async def get_object_record(self, location: ObjectLocation) -> StoredObject:
        async with self.lock:
            return self.get_stored_object(location)

    async def delete_object(self, location: ObjectLocation) -> None:
        async with self.lock:
            bucket = self.get_bucket(location.bucket_name)
            stored_object = bucket.objects.pop(location.key, None)
            if stored_object is not None:
                await self.remove_photos(bucket, stored_object=stored_object)

    async def copy_object(
        self,
        source: ObjectLocation,
        *,
        request_description: ObjectDescription,
        metadata_directive: MetadataDirective,
    ) -> StoredObject:
        source_content = await self.get_object_content(source)
        source_header = source_content.stored_object.header
        description = metadata_directive.choose_description(
            source_description=ObjectDescription(
                bucket=request_description.bucket,
                key=request_description.key,
                content_type=source_header.content_type,
                metadata=source_header.metadata,
            ),
            request_description=request_description,
        )
        return await self.put_object(source_content.data, description=description)

    async def list_objects_page(
        self,
        bucket_name: str,
        *,
        prefix: str,
        delimiter: str,
        start_after: str,
        maximum_keys: int,
    ) -> ObjectListingPage:
        async with self.lock:
            objects = self.get_bucket(bucket_name).objects
            stored_objects = [objects[key] for key in sorted(objects) if key.startswith(prefix) and key > start_after]
        return paginate_objects(stored_objects, prefix=prefix, delimiter=delimiter, maximum_keys=maximum_keys)

    async def create_multipart_upload(self, description: ObjectDescription) -> str:
        async with self.lock:
            self.get_bucket(description.bucket)
            upload_id = uuid.uuid4().hex
            self.multipart_uploads[upload_id] = MultipartUpload(description=description)
            return upload_id

    async def upload_part(self, data: bytes, *, upload_id: str, location: ObjectLocation, part_number: int) -> str:
        async with self.lock:
            self.get_multipart_upload(upload_id, location=location).parts[part_number] = data
        return hashlib.md5(data, usedforsecurity=False).hexdigest()

    async def copy_part(
        self,
        source: ObjectLocation,
        *,
        requested_byte_range: RequestedByteRange | None,
        upload_id: str,
        location: ObjectLocation,
        part_number: int,
    ) -> str:
        source_data = (await self.get_object_content(source)).data
        part_data = (
            source_data
            if requested_byte_range is None
            else requested_byte_range.resolve(total_size_bytes=len(source_data)).slice_bytes(source_data)
        )
        return await self.upload_part(part_data, upload_id=upload_id, location=location, part_number=part_number)

    async def complete_multipart_upload(
        self,
        upload_id: str,
        *,
        location: ObjectLocation,
        part_numbers: list[int],
    ) -> StoredObject:
        async with self.lock:
            upload = self.get_multipart_upload(upload_id, location=location)
            missing_part_numbers = sorted(set(part_numbers) - upload.parts.keys())
            if missing_part_numbers or not part_numbers:
                message = f"Parts {missing_part_numbers} were never uploaded"
                raise InvalidPartError(message)
            del self.multipart_uploads[upload_id]
        data = b"".join(upload.parts[part_number] for part_number in sorted(set(part_numbers)))
        return await self.put_object(data, description=upload.description)

    async def abort_multipart_upload(self, upload_id: str, *, location: ObjectLocation) -> None:
        async with self.lock:
            self.get_multipart_upload(upload_id, location=location)
            del self.multipart_uploads[upload_id]

    async def reload_from_photo_folder(self) -> None:
        if self.photo_folder is None:
            return
        photo_files = await self.album_file_lane.run(self.photo_folder.list_photo_files)
        discovered_frames = await self.carrier_lane.run(discover_frames, photo_files)
        async with self.lock:
            for frames in select_latest_complete_objects(discovered_frames):
                first_header = frames[0].header
                album_name = frames[0].photo_file.album_name
                bucket = self.buckets.setdefault(first_header.bucket, Bucket(created_at=first_header.created_at))
                for frame in frames:
                    bucket.photos[frame.photo_file.photo_id] = frame.photo_file.png_bytes
                bucket.album_photo_counts[album_name] = bucket.album_photo_counts.get(album_name, 0) + len(frames)
                bucket.objects[first_header.key] = StoredObject(
                    header=first_header,
                    album_name=album_name,
                    photo_ids=tuple(frame.photo_file.photo_id for frame in frames),
                )
