import mimetypes
import re
from typing import Annotated
from urllib.parse import unquote
from xml.etree.ElementTree import Element, ParseError, fromstring

from fastapi import Depends, Request

from icloud_s3.albums.service import SharedAlbumService
from icloud_s3.albums.types import ObjectLocation, RequestedByteRange
from icloud_s3.carrier.types import ObjectDescription
from icloud_s3.shared.errors import EntityTooLargeError, InvalidArgumentError

MAXIMUM_REQUEST_BODY_BYTES = 64 * 1024 * 1024
BYTE_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")


def get_album_service(request: Request) -> SharedAlbumService:
    album_service = request.app.state.album_service
    if not isinstance(album_service, SharedAlbumService):  # Starlette's app.state is untyped
        message = "the lifespan did not install the album service"
        raise TypeError(message)
    return album_service


AlbumServiceDependency = Annotated[SharedAlbumService, Depends(get_album_service)]


def decode_aws_chunked_body(body: bytes) -> bytes:
    decoded_parts: list[bytes] = []
    position = 0
    while (line_end := body.find(b"\r\n", position)) != -1:
        try:
            chunk_size = int(body[position:line_end].split(b";")[0], 16)
        except ValueError as error:
            message = "malformed aws-chunked body"
            raise InvalidArgumentError(message) from error
        if chunk_size == 0:
            break
        chunk_start = line_end + 2
        decoded_parts.append(body[chunk_start : chunk_start + chunk_size])
        position = chunk_start + chunk_size + 2
    return b"".join(decoded_parts)


async def read_object_body(request: Request) -> bytes:
    body_parts: list[bytes] = []
    received_bytes = 0
    async for body_part in request.stream():
        received_bytes += len(body_part)
        if received_bytes > MAXIMUM_REQUEST_BODY_BYTES:
            message = f"Request bodies are limited to {MAXIMUM_REQUEST_BODY_BYTES} bytes"
            raise EntityTooLargeError(message)
        body_parts.append(body_part)
    body = b"".join(body_parts)
    is_aws_chunked = request.headers.get("x-amz-content-sha256", "").startswith(
        "STREAMING-",
    ) or "aws-chunked" in request.headers.get("content-encoding", "")
    return decode_aws_chunked_body(body) if is_aws_chunked else body


ObjectBodyDependency = Annotated[bytes, Depends(read_object_body)]


def describe_object_from_request(request: Request, *, location: ObjectLocation) -> ObjectDescription:
    requested_content_type = request.headers.get("content-type", "")
    return ObjectDescription(
        bucket=location.bucket_name,
        key=location.key,
        content_type=(
            requested_content_type
            if requested_content_type not in {"", "binary/octet-stream"}
            else mimetypes.guess_type(location.key)[0] or "application/octet-stream"
        ),
        metadata=dict(
            sorted(
                (name.removeprefix("x-amz-meta-"), value)
                for name, value in request.headers.items()
                if name.startswith("x-amz-meta-")
            ),
        ),
    )


def parse_copy_source(copy_source: str) -> ObjectLocation:
    bucket_name, _, key = unquote(copy_source.split("?", 1)[0]).lstrip("/").partition("/")
    return ObjectLocation(bucket_name=bucket_name, key=key)


def find_requested_byte_range(range_header: str | None) -> RequestedByteRange | None:
    range_match = BYTE_RANGE_PATTERN.fullmatch((range_header or "").strip())
    if range_match is None or range_match.groups() == ("", ""):
        return None
    first_byte, last_byte = range_match.groups()
    return RequestedByteRange(
        first_byte=int(first_byte) if first_byte else None,
        last_byte=int(last_byte) if last_byte else None,
    )


def list_xml_texts(xml_body: bytes, *, element_name: str) -> list[str]:
    try:
        root: Element = fromstring(xml_body)  # noqa: S314 - expat 2.4.1+ blocks entity expansion: https://docs.python.org/3/library/xml.html#xml-vulnerabilities
    except ParseError as error:
        raise InvalidArgumentError(str(error)) from error
    return [element.text or "" for element in root.iter() if element.tag.rsplit("}", 1)[-1] == element_name]


def list_part_numbers(xml_body: bytes) -> list[int]:
    part_number_texts = list_xml_texts(xml_body, element_name="PartNumber")
    if not all(part_number_text.isdecimal() for part_number_text in part_number_texts):
        message = "PartNumber must be a positive integer"
        raise InvalidArgumentError(message)
    return [int(part_number_text) for part_number_text in part_number_texts]
