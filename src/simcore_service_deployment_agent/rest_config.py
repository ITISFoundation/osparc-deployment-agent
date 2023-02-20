""" rest subsystem's configuration

    - constants
    - config-file schema
"""
import trafaret as T
from servicelib.aiohttp.application_keys import APP_OPENAPI_SPECS_KEY

assert APP_OPENAPI_SPECS_KEY  # nosec
CONFIG_SECTION_NAME = "rest"

schema = T.Dict(
    {
        "version": T.Enum("v0"),
        "location": T.Or(
            T.String, T.URL
        ),  # either path or url should contain version in it
    }
)

__all__: tuple[str, ...] = ("APP_OPENAPI_SPECS_KEY",)
