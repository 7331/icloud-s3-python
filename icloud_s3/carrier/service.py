from collections.abc import Iterable
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import struct
import uuid
import zlib

from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from icloud_s3.carrier.errors import CarrierError
from icloud_s3.carrier.types import DecodedFrame, DecodedObject, EncodedFrame, FrameHeader, ObjectDescription

MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS = 2048
BANNER_HEIGHT_PIXELS = 64
MINIMUM_DATA_ROWS = 96
FRAME_MAGIC = b"ICS3\x00\x01"
FRAME_PREAMBLE = struct.Struct(">6sIII")
MAXIMUM_CHUNK_SIZE_BYTES = (
    MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS * (MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS - BANNER_HEIGHT_PIXELS) * 3 - 8192
)
DEFAULT_CHUNK_SIZE_BYTES = 2 * 1024 * 1024
BANNER_BACKGROUND_COLOR = (12, 17, 29)
BANNER_ACCENT_COLOR = (56, 189, 248)
BANNER_TEXT_COLORS = ((56, 189, 248), (248, 250, 252), (148, 163, 184))
PADDING_BYTE = b"\x10"


def encode_object(
    data: bytes,
    *,
    description: ObjectDescription,
    maximum_chunk_size_bytes: int,
) -> list[EncodedFrame]:
    chunk_size_bytes = min(max(maximum_chunk_size_bytes, 64), MAXIMUM_CHUNK_SIZE_BYTES)
    chunks = [data[offset : offset + chunk_size_bytes] for offset in range(0, len(data), chunk_size_bytes)] or [b""]
    object_id = str(uuid.uuid4())
    etag = hashlib.md5(data, usedforsecurity=False).hexdigest()
    object_sha256 = hashlib.sha256(data).hexdigest()
    created_at = datetime.now(UTC)
    return [
        encode_frame(
            FrameHeader(
                version=1,
                bucket=description.bucket,
                key=description.key,
                object_id=object_id,
                content_type=description.content_type,
                total_size=len(data),
                chunk_index=chunk_index,
                total_chunks=len(chunks),
                chunk_size=len(chunk),
                etag=etag,
                object_sha256=object_sha256,
                created_at=created_at,
                metadata=description.metadata,
            ),
            chunk=chunk,
        )
        for chunk_index, chunk in enumerate(chunks)
    ]


def encode_frame(header: FrameHeader, *, chunk: bytes) -> EncodedFrame:
    header_json = header.model_dump_json().encode()
    checksum = zlib.crc32(chunk, zlib.crc32(header_json))
    stream = FRAME_PREAMBLE.pack(FRAME_MAGIC, len(header_json), len(chunk), checksum) + header_json + chunk
    required_pixels = -(-len(stream) // 3)
    width = choose_frame_width(required_pixels)
    data_rows = max(-(-required_pixels // width), MINIMUM_DATA_ROWS)
    height = BANNER_HEIGHT_PIXELS + data_rows
    if height > MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS:
        message = f"frame {width}x{height} exceeds the Shared Album 2048px limit"
        raise CarrierError(message)
    image = Image.new("RGB", (width, height), BANNER_BACKGROUND_COLOR)
    data_region = stream.ljust(width * data_rows * 3, PADDING_BYTE)
    image.paste(Image.frombytes("RGB", (width, data_rows), data_region), (0, BANNER_HEIGHT_PIXELS))
    draw_banner(image, header=header)
    png_buffer = BytesIO()
    image.save(png_buffer, format="PNG")
    return EncodedFrame(header=header, png_bytes=png_buffer.getvalue())


def choose_frame_width(required_pixels: int) -> int:
    if required_pixels <= 560 * 120:
        return 560
    if required_pixels <= 1024 * 512:
        return 1024
    return MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS


def draw_banner(image: Image.Image, *, header: FrameHeader) -> None:
    draw = ImageDraw.Draw(image)
    draw.line([(0, BANNER_HEIGHT_PIXELS - 1), (image.width, BANNER_HEIGHT_PIXELS - 1)], fill=BANNER_ACCENT_COLOR)
    draw.rectangle([(8, 6), (11, BANNER_HEIGHT_PIXELS - 7)], fill=BANNER_ACCENT_COLOR)
    font = ImageFont.load_default(size=13)
    banner_lines = (
        f"ICLOUD-S3 // SHARED ALBUM OBJECT  [CHUNK {header.chunk_index + 1}/{header.total_chunks}]",
        f"s3://{header.bucket}/{header.key}",
        f"{header.total_size} B  |  {header.content_type.upper()}  |  ETAG {header.etag[:12]}",
    )
    for line_index, (line_text, line_color) in enumerate(zip(banner_lines, BANNER_TEXT_COLORS, strict=True)):
        draw.text((20, 4 + line_index * 18), line_text, fill=line_color, font=font)


def read_data_region(png_bytes: bytes) -> bytes:
    try:
        with Image.open(BytesIO(png_bytes), formats=["PNG"]) as image:
            width, height = image.size
            if height <= BANNER_HEIGHT_PIXELS or max(width, height) > MAXIMUM_SHARED_ALBUM_DIMENSION_PIXELS:
                message = f"{width}x{height} is not a carrier frame size"
                raise CarrierError(message)
            return image.convert("RGB").crop((0, BANNER_HEIGHT_PIXELS, width, height)).tobytes()
    except (OSError, Image.DecompressionBombError) as error:
        raise CarrierError(str(error)) from error


def decode_frame(png_bytes: bytes) -> DecodedFrame:
    data_region = read_data_region(png_bytes)
    if len(data_region) < FRAME_PREAMBLE.size:
        message = "frame too small for its preamble"
        raise CarrierError(message)
    magic, header_length, chunk_length, expected_checksum = FRAME_PREAMBLE.unpack_from(data_region)
    if magic != FRAME_MAGIC:
        message = "invalid frame magic: not an icloud-s3 carrier photo"
        raise CarrierError(message)
    chunk_start = FRAME_PREAMBLE.size + header_length
    chunk_end = chunk_start + chunk_length
    if chunk_end > len(data_region):
        message = f"declared lengths exceed pixel capacity {len(data_region)}"
        raise CarrierError(message)
    header_json = data_region[FRAME_PREAMBLE.size : chunk_start]
    chunk = data_region[chunk_start:chunk_end]
    actual_checksum = zlib.crc32(chunk, zlib.crc32(header_json))
    if actual_checksum != expected_checksum:
        message = f"CRC32 mismatch: expected {expected_checksum:#010x}, got {actual_checksum:#010x}"
        raise CarrierError(message)
    try:
        header = FrameHeader.model_validate_json(header_json)
    except ValidationError as error:
        raise CarrierError(str(error)) from error
    return DecodedFrame(header=header, chunk=chunk)


def decode_object(png_frames: Iterable[bytes]) -> DecodedObject:
    frames = sorted((decode_frame(png_bytes) for png_bytes in png_frames), key=lambda frame: frame.header.chunk_index)
    if not frames:
        message = "no frames to decode"
        raise CarrierError(message)
    first_header = frames[0].header
    if [frame.header.chunk_index for frame in frames] != list(range(first_header.total_chunks)):
        message = f"incomplete chunk set: expected {first_header.total_chunks}, got {len(frames)}"
        raise CarrierError(message)
    data = b"".join(frame.chunk for frame in frames)
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != first_header.object_sha256:
        message = f"SHA-256 mismatch: expected {first_header.object_sha256}, got {actual_sha256}"
        raise CarrierError(message)
    return DecodedObject(header=first_header, data=data)
