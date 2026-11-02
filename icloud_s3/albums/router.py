from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response

from icloud_s3.albums.dependencies import (
    AlbumServiceDependency,
    ObjectBodyDependency,
    describe_object_from_request,
    find_requested_byte_range,
    list_part_numbers,
    list_xml_texts,
    parse_copy_source,
)
from icloud_s3.albums.enums import CarrierFormat, MetadataDirective
from icloud_s3.albums.presenters import (
    present_bucket_list,
    present_bucket_location,
    present_completed_upload,
    present_copy_part_result,
    present_copy_result,
    present_deleted_keys,
    present_multipart_upload,
    present_object_headers,
    present_object_listing_version_one,
    present_object_listing_version_two,
)
from icloud_s3.albums.service import SharedAlbumService
from icloud_s3.albums.types import ObjectLocation
from icloud_s3.shared.errors import InvalidArgumentError
from icloud_s3.shared.xml_responses import build_xml_response

MAXIMUM_LIST_KEYS = 1000

UploadIdQuery = Annotated[str | None, Query(alias="uploadId")]
PartNumberQuery = Annotated[int | None, Query(alias="partNumber", ge=1, le=10000)]
CopySourceHeader = Annotated[str | None, Header(alias="x-amz-copy-source")]
CopySourceRangeHeader = Annotated[str | None, Header(alias="x-amz-copy-source-range")]
MetadataDirectiveHeader = Annotated[MetadataDirective, Header(alias="x-amz-metadata-directive")]
RangeHeader = Annotated[str | None, Header(alias="range")]

router = APIRouter(tags=["s3"])


@router.get("/")
async def list_buckets(*, album_service: AlbumServiceDependency) -> Response:
    return build_xml_response(present_bucket_list(await album_service.list_buckets()))


@router.put("/{bucket_name}")
@router.put("/{bucket_name}/")
async def create_bucket(*, bucket_name: str, album_service: AlbumServiceDependency) -> Response:
    await album_service.create_bucket(bucket_name)
    return Response(status_code=HTTPStatus.OK, headers={"Location": f"/{bucket_name}"})


@router.head("/{bucket_name}")
@router.head("/{bucket_name}/")
async def head_bucket(*, bucket_name: str, album_service: AlbumServiceDependency) -> Response:
    await album_service.get_bucket_summary(bucket_name)
    return Response(status_code=HTTPStatus.OK)


@router.delete("/{bucket_name}")
@router.delete("/{bucket_name}/")
async def delete_bucket(*, bucket_name: str, album_service: AlbumServiceDependency) -> Response:
    await album_service.delete_bucket(bucket_name)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get("/{bucket_name}")
@router.get("/{bucket_name}/")
async def list_objects(
    *,
    bucket_name: str,
    album_service: AlbumServiceDependency,
    prefix: str = "",
    delimiter: str = "",
    maximum_keys: Annotated[int, Query(alias="max-keys", ge=0)] = MAXIMUM_LIST_KEYS,
    list_type: Annotated[int | None, Query(alias="list-type")] = None,
    continuation_token: Annotated[str | None, Query(alias="continuation-token")] = None,
    start_after: Annotated[str, Query(alias="start-after")] = "",
    marker: str = "",
    location: str | None = None,
) -> Response:
    if location is not None:
        await album_service.get_bucket_summary(bucket_name)
        return build_xml_response(present_bucket_location())
    page_size = min(maximum_keys, MAXIMUM_LIST_KEYS)
    is_version_two = list_type == 2  # noqa: PLR2004 - S3's list-type=2 selects ListObjectsV2
    page = await album_service.list_objects_page(
        bucket_name,
        prefix=prefix,
        delimiter=delimiter,
        start_after=(continuation_token or start_after) if is_version_two else marker,
        maximum_keys=page_size,
    )
    if is_version_two:
        return build_xml_response(
            present_object_listing_version_two(page, bucket_name=bucket_name, prefix=prefix, maximum_keys=page_size),
        )
    return build_xml_response(
        present_object_listing_version_one(
            page,
            bucket_name=bucket_name,
            prefix=prefix,
            marker=marker,
            maximum_keys=page_size,
        ),
    )


@router.post("/{bucket_name}")
@router.post("/{bucket_name}/")
async def delete_objects(
    *,
    bucket_name: str,
    album_service: AlbumServiceDependency,
    body: ObjectBodyDependency,
    delete: Annotated[str | None, Query()] = None,
) -> Response:
    if delete is None:
        message = "POST on a bucket needs ?delete"
        raise InvalidArgumentError(message)
    keys = list_xml_texts(body, element_name="Key")
    for key in keys:
        await album_service.delete_object(ObjectLocation(bucket_name=bucket_name, key=key))
    return build_xml_response(present_deleted_keys(keys))


async def upload_part_from_request(
    album_service: SharedAlbumService,
    *,
    location: ObjectLocation,
    body: bytes,
    upload_id: str,
    part_number: int,
    copy_source: str | None,
    copy_source_range: str | None,
) -> Response:
    if copy_source is None:
        part_etag = await album_service.upload_part(
            body,
            upload_id=upload_id,
            location=location,
            part_number=part_number,
        )
        return Response(status_code=HTTPStatus.OK, headers={"ETag": f'"{part_etag}"'})
    part_etag = await album_service.copy_part(
        parse_copy_source(copy_source),
        requested_byte_range=find_requested_byte_range(copy_source_range),
        upload_id=upload_id,
        location=location,
        part_number=part_number,
    )
    return build_xml_response(present_copy_part_result(part_etag))


@router.put("/{bucket_name}/{key:path}")
async def put_object(
    *,
    bucket_name: str,
    key: str,
    request: Request,
    album_service: AlbumServiceDependency,
    body: ObjectBodyDependency,
    upload_id: UploadIdQuery = None,
    part_number: PartNumberQuery = None,
    copy_source: CopySourceHeader = None,
    copy_source_range: CopySourceRangeHeader = None,
    metadata_directive: MetadataDirectiveHeader = MetadataDirective.COPY,
) -> Response:
    location = ObjectLocation(bucket_name=bucket_name, key=key)
    if upload_id is not None and part_number is not None:
        return await upload_part_from_request(
            album_service,
            location=location,
            body=body,
            upload_id=upload_id,
            part_number=part_number,
            copy_source=copy_source,
            copy_source_range=copy_source_range,
        )
    request_description = describe_object_from_request(request, location=location)
    if copy_source is not None:
        copied_object = await album_service.copy_object(
            parse_copy_source(copy_source),
            request_description=request_description,
            metadata_directive=metadata_directive,
        )
        return build_xml_response(present_copy_result(copied_object))
    stored_object = await album_service.put_object(body, description=request_description)
    return Response(status_code=HTTPStatus.OK, headers=present_object_headers(stored_object) | {"Content-Length": "0"})


@router.post("/{bucket_name}/{key:path}")
async def post_object(
    *,
    bucket_name: str,
    key: str,
    request: Request,
    album_service: AlbumServiceDependency,
    body: ObjectBodyDependency,
    uploads: Annotated[str | None, Query()] = None,
    upload_id: UploadIdQuery = None,
) -> Response:
    location = ObjectLocation(bucket_name=bucket_name, key=key)
    if uploads is not None:
        created_upload_id = await album_service.create_multipart_upload(
            describe_object_from_request(request, location=location),
        )
        return build_xml_response(present_multipart_upload(created_upload_id, bucket_name=bucket_name, key=key))
    if upload_id is None:
        message = "POST on an object needs ?uploads or ?uploadId"
        raise InvalidArgumentError(message)
    completed_object = await album_service.complete_multipart_upload(
        upload_id,
        location=location,
        part_numbers=list_part_numbers(body),
    )
    return build_xml_response(present_completed_upload(completed_object))


@router.get("/{bucket_name}/{key:path}")
async def get_object(
    *,
    bucket_name: str,
    key: str,
    album_service: AlbumServiceDependency,
    carrier_format: Annotated[CarrierFormat | None, Query(alias="format")] = None,
    chunk: Annotated[int, Query(ge=0)] = 0,
    range_header: RangeHeader = None,
) -> Response:
    location = ObjectLocation(bucket_name=bucket_name, key=key)
    if carrier_format is not None:
        carrier_photo = await album_service.get_carrier_photo(location, chunk_index=chunk)
        return Response(
            content=carrier_photo.png_bytes,
            media_type="image/png",
            headers={"Cache-Control": "no-cache", "ETag": f'"{carrier_photo.stored_object.header.etag}"'},
        )
    object_content = await album_service.get_object_content(location)
    object_headers = present_object_headers(object_content.stored_object)
    requested_byte_range = find_requested_byte_range(range_header)
    if requested_byte_range is None:
        return Response(content=object_content.data, headers=object_headers)
    total_size_bytes = len(object_content.data)
    byte_range = requested_byte_range.resolve(total_size_bytes=total_size_bytes)
    partial_data = byte_range.slice_bytes(object_content.data)
    return Response(
        content=partial_data,
        status_code=HTTPStatus.PARTIAL_CONTENT,
        headers=object_headers
        | {
            "Content-Length": str(len(partial_data)),
            "Content-Range": f"bytes {byte_range.first_byte}-{byte_range.last_byte}/{total_size_bytes}",
        },
    )


@router.head("/{bucket_name}/{key:path}")
async def head_object(*, bucket_name: str, key: str, album_service: AlbumServiceDependency) -> Response:
    stored_object = await album_service.get_object_record(ObjectLocation(bucket_name=bucket_name, key=key))
    return Response(status_code=HTTPStatus.OK, headers=present_object_headers(stored_object))


@router.delete("/{bucket_name}/{key:path}")
async def delete_object(
    *,
    bucket_name: str,
    key: str,
    album_service: AlbumServiceDependency,
    upload_id: UploadIdQuery = None,
) -> Response:
    location = ObjectLocation(bucket_name=bucket_name, key=key)
    if upload_id is not None:
        await album_service.abort_multipart_upload(upload_id, location=location)
    else:
        await album_service.delete_object(location)
    return Response(status_code=HTTPStatus.NO_CONTENT)
