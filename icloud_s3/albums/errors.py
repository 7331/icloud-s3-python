from http import HTTPStatus

from icloud_s3.shared.errors import AppError


class NoSuchBucketError(AppError):
    code = "NoSuchBucket"
    status = HTTPStatus.NOT_FOUND


class NoSuchKeyError(AppError):
    code = "NoSuchKey"
    status = HTTPStatus.NOT_FOUND


class NoSuchUploadError(AppError):
    code = "NoSuchUpload"
    status = HTTPStatus.NOT_FOUND


class BucketNotEmptyError(AppError):
    code = "BucketNotEmpty"
    status = HTTPStatus.CONFLICT


class InvalidBucketNameError(AppError):
    code = "InvalidBucketName"
    status = HTTPStatus.BAD_REQUEST


class TooManyBucketsError(AppError):
    code = "TooManyBuckets"
    status = HTTPStatus.BAD_REQUEST


class InvalidPartError(AppError):
    code = "InvalidPart"
    status = HTTPStatus.BAD_REQUEST


class InvalidRangeError(AppError):
    code = "InvalidRange"
    status = HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE


class AlbumFullError(AppError):
    code = "QuotaExceeded"
    status = HTTPStatus.INSUFFICIENT_STORAGE
