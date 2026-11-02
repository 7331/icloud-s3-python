from icloud_s3.shared.errors import AppError


class CarrierError(AppError):
    code = "InternalError"
