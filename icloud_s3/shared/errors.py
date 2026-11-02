from http import HTTPStatus


class AppError(Exception):
    code: str = "InternalError"
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidArgumentError(AppError):
    code = "InvalidArgument"
    status = HTTPStatus.BAD_REQUEST


class EntityTooLargeError(AppError):
    code = "EntityTooLarge"
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class SlowDownError(AppError):
    code = "SlowDown"
    status = HTTPStatus.SERVICE_UNAVAILABLE
