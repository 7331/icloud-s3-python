from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape

from icloud_s3.albums.types import BucketSummary, ObjectListingPage, StoredObject
from icloud_s3.shared.xml_responses import S3_NAMESPACE


def format_rfc3339(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def present_object_headers(stored_object: StoredObject) -> dict[str, str]:
    header = stored_object.header
    return {
        "Content-Type": header.content_type,
        "Content-Length": str(header.total_size),
        "Accept-Ranges": "bytes",
        "ETag": f'"{header.etag}"',
        "Last-Modified": format_datetime(header.created_at, usegmt=True),
        "x-icloud-s3-photo-frames": str(len(stored_object.photo_ids)),
        "x-icloud-s3-shared-album": stored_object.album_name,
        "x-icloud-s3-sha256": header.object_sha256,
        **{f"x-amz-meta-{name}": value for name, value in header.metadata.items()},
    }


def present_bucket_list(buckets: list[BucketSummary]) -> str:
    bucket_elements = "".join(
        f"<Bucket><Name>{escape(bucket.name)}</Name>"
        f"<CreationDate>{format_rfc3339(bucket.created_at)}</CreationDate></Bucket>"
        for bucket in buckets
    )
    return (
        f'<ListAllMyBucketsResult xmlns="{S3_NAMESPACE}">'
        "<Owner><ID>icloud-shared-album-owner</ID><DisplayName>icloud-s3</DisplayName></Owner>"
        f"<Buckets>{bucket_elements}</Buckets></ListAllMyBucketsResult>"
    )


def present_bucket_location() -> str:
    return f'<LocationConstraint xmlns="{S3_NAMESPACE}"></LocationConstraint>'


def present_listing_entries(page: ObjectListingPage) -> str:
    content_elements = "".join(
        f"<Contents><Key>{escape(stored_object.header.key)}</Key>"
        f"<LastModified>{format_rfc3339(stored_object.header.created_at)}</LastModified>"
        f"<ETag>&quot;{stored_object.header.etag}&quot;</ETag><Size>{stored_object.header.total_size}</Size>"
        "<StorageClass>ICLOUD_SHARED_ALBUM</StorageClass></Contents>"
        for stored_object in page.contents
    )
    prefix_elements = "".join(
        f"<CommonPrefixes><Prefix>{escape(common_prefix)}</Prefix></CommonPrefixes>"
        for common_prefix in page.common_prefixes
    )
    return f"<IsTruncated>{str(page.is_truncated).lower()}</IsTruncated>{content_elements}{prefix_elements}"


def present_object_listing_version_two(
    page: ObjectListingPage,
    *,
    bucket_name: str,
    prefix: str,
    maximum_keys: int,
) -> str:
    next_token = (
        f"<NextContinuationToken>{escape(page.last_listed_key)}</NextContinuationToken>" if page.is_truncated else ""
    )
    return (
        f'<ListBucketResult xmlns="{S3_NAMESPACE}"><Name>{escape(bucket_name)}</Name>'
        f"<Prefix>{escape(prefix)}</Prefix><KeyCount>{len(page.contents) + len(page.common_prefixes)}</KeyCount>"
        f"{next_token}<MaxKeys>{maximum_keys}</MaxKeys>{present_listing_entries(page)}</ListBucketResult>"
    )


def present_object_listing_version_one(
    page: ObjectListingPage,
    *,
    bucket_name: str,
    prefix: str,
    marker: str,
    maximum_keys: int,
) -> str:
    next_marker = f"<NextMarker>{escape(page.last_listed_key)}</NextMarker>" if page.is_truncated else ""
    return (
        f'<ListBucketResult xmlns="{S3_NAMESPACE}"><Name>{escape(bucket_name)}</Name>'
        f"<Prefix>{escape(prefix)}</Prefix><Marker>{escape(marker)}</Marker>{next_marker}"
        f"<MaxKeys>{maximum_keys}</MaxKeys>{present_listing_entries(page)}</ListBucketResult>"
    )


def present_deleted_keys(keys: list[str]) -> str:
    deleted_elements = "".join(f"<Deleted><Key>{escape(key)}</Key></Deleted>" for key in keys)
    return f'<DeleteResult xmlns="{S3_NAMESPACE}">{deleted_elements}</DeleteResult>'


def present_copy_result(stored_object: StoredObject) -> str:
    return (
        f'<CopyObjectResult xmlns="{S3_NAMESPACE}">'
        f"<LastModified>{format_rfc3339(stored_object.header.created_at)}</LastModified>"
        f"<ETag>&quot;{stored_object.header.etag}&quot;</ETag></CopyObjectResult>"
    )


def present_copy_part_result(part_etag: str) -> str:
    return f'<CopyPartResult xmlns="{S3_NAMESPACE}"><ETag>&quot;{part_etag}&quot;</ETag></CopyPartResult>'


def present_multipart_upload(upload_id: str, *, bucket_name: str, key: str) -> str:
    return (
        f'<InitiateMultipartUploadResult xmlns="{S3_NAMESPACE}"><Bucket>{escape(bucket_name)}</Bucket>'
        f"<Key>{escape(key)}</Key><UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>"
    )


def present_completed_upload(stored_object: StoredObject) -> str:
    header = stored_object.header
    return (
        f'<CompleteMultipartUploadResult xmlns="{S3_NAMESPACE}">'
        f"<Location>/{escape(header.bucket)}/{escape(header.key)}</Location>"
        f"<Bucket>{escape(header.bucket)}</Bucket><Key>{escape(header.key)}</Key>"
        f"<ETag>&quot;{header.etag}&quot;</ETag></CompleteMultipartUploadResult>"
    )
