from http import HTTPStatus
from xml.sax.saxutils import escape

from fastapi import Response

from icloud_s3.shared.errors import AppError

S3_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


def build_xml_response(xml_body: str, *, status: HTTPStatus = HTTPStatus.OK) -> Response:
    return Response(content=XML_DECLARATION + xml_body, status_code=status, media_type="application/xml")


def build_error_response(error: AppError, *, resource: str) -> Response:
    return build_xml_response(
        f"<Error><Code>{error.code}</Code><Message>{escape(error.message)}</Message>"
        f"<Resource>{escape(resource)}</Resource><RequestId>icloud-s3</RequestId></Error>",
        status=error.status,
    )
