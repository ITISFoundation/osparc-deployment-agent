from typing import Any, TypedDict

ServiceName = str
ServiceSpecsDict = dict[str, Any]
VolumeName = str


class ComposeSpecsDict(TypedDict):
    version: str
    services: dict[ServiceName, ServiceSpecsDict]
    volumes: dict[VolumeName, Any]
