from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError

from icloud_s3.shared.errors import AppError, InvalidArgumentError
from icloud_s3.shared.xml_responses import build_error_response


async def handle_app_error(request: Request, error: Exception) -> Response:  # noqa: PLR0917 - Starlette passes (request, error) positionally
    app_error = error if isinstance(error, AppError) else AppError(str(error))  # Starlette types handlers by Exception
    return build_error_response(app_error, resource=request.url.path)


async def handle_request_validation_error(request: Request, error: Exception) -> Response:  # noqa: PLR0917 - Starlette passes (request, error) positionally
    return build_error_response(InvalidArgumentError(str(error)), resource=request.url.path)


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(AppError, handle_app_error)
    application.add_exception_handler(RequestValidationError, handle_request_validation_error)
