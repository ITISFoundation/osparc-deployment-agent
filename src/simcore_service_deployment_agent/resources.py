""" Access to data resources installed with this package

"""
from servicelib.resources import ResourcesFacade

from .settings import RSC_CONFIG_DIR_KEY

resources = ResourcesFacade(
    package_name=__name__,
    distribution_name="simcore-service-deployment-agent",
    config_folder=RSC_CONFIG_DIR_KEY,
)

assert RSC_CONFIG_DIR_KEY  # nosec

__all__: tuple[str, ...] = (
    "resources",
    "RSC_CONFIG_DIR_KEY",
)
