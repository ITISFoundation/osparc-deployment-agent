from datetime import datetime
from typing import Any, TypedDict

from pydantic import BaseModel, Field

ServiceName = str
ServiceSpecsDict = dict[str, Any]
VolumeName = str


class ComposeSpecsDict(TypedDict):
    version: str
    services: dict[ServiceName, ServiceSpecsDict]
    volumes: dict[VolumeName, Any]


class WebserverExtraEnvirons(BaseModel):
    SIMCORE_VCS_RELEASE_TAG: str = Field(
        description="Name of the tag that makrs this release or None if undefined",
    )
    SIMCORE_VCS_RELEASE_DATE: datetime = Field(
        description="Release date. It corresponds to the tag's creation date",
    )

    class Config:
        schema_extra = {
            "example": {
                "SIMCORE_VCS_RELEASE_TAG": "ResistanceIsFutile10",
                "SIMCORE_VCS_RELEASE_DATE": "2023-02-10T18:03:35.957601",
            }
        }
