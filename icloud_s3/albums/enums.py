from enum import StrEnum

from icloud_s3.carrier.types import ObjectDescription


class MetadataDirective(StrEnum):
    COPY = "COPY"
    REPLACE = "REPLACE"

    def choose_description(
        self,
        *,
        source_description: ObjectDescription,
        request_description: ObjectDescription,
    ) -> ObjectDescription:
        match self:
            case MetadataDirective.COPY:
                return source_description
            case MetadataDirective.REPLACE:
                return request_description


class CarrierFormat(StrEnum):
    PNG = "png"
