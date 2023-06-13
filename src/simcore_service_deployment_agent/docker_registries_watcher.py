import logging
from collections.abc import Iterator
from contextlib import contextmanager

from tenacity import retry
from tenacity.after import after_log
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_random

import docker
import docker.errors
from docker import DockerClient

from .models import ComposeSpecsDict
from .subtask import SubTask

log = logging.getLogger(__name__)

NUMBER_OF_ATTEMPS = 5
MAX_TIME_TO_WAIT_S = 10


@contextmanager
def docker_client(registries: list[dict]) -> Iterator[DockerClient]:
    log.debug("creating docker client..")
    client = docker.from_env()
    log.debug("docker client ping returns: %s", client.ping())
    for registry in registries:
        log.debug("logging in %s..", registry["url"])
        client.login(
            registry=registry["url"],
            username=registry["username"],
            password=registry["password"],
        )
        log.debug("login done")

    try:
        yield client
    finally:
        pass


class DockerRegistriesWatcher(SubTask):
    def __init__(self, app_config: dict, stack_cfg: ComposeSpecsDict):
        super().__init__(name="dockerhub repo watcher")
        # get all the private registries
        self.private_registries = app_config["main"]["docker_private_registries"]
        # get all the images to check for
        self.watched_docker_images = []
        if "services" in stack_cfg:
            for service_name in stack_cfg["services"].keys():
                if "image" in stack_cfg["services"][service_name]:
                    image_url = stack_cfg["services"][service_name]["image"]
                    self.watched_docker_images.append({"image": image_url})
                else:
                    raise ValueError(  # pylint: disable=raising-format-tuple
                        "Service %s in generated stack file has no docker image specififed.",
                        service_name,
                    )

    async def init(self):
        log.info("initialising docker watcher..")
        with docker_client(self.private_registries) as client:
            for docker_image in self.watched_docker_images:
                try:
                    registry_data = client.images.get_registry_data(
                        docker_image["image"]
                    )
                    log.debug(
                        "succesfully accessed image %s: %s",
                        docker_image["image"],
                        registry_data.attrs,
                    )
                    docker_image["registry_data_attrs"] = registry_data.attrs
                except docker.errors.APIError:
                    # in case a new service that is not yet in the registry was added
                    log.warning(
                        "could not find image %s, maybe a new image was added to the stack??",
                        docker_image["image"],
                    )
                    # We null the content of repo["registry_data_attrs"].
                    # In check_for_changes(), it is expected that repo["registry_data_attrs"] is a dict with a key
                    # named "Descriptor", so we add it empty.
                    docker_image["registry_data_attrs"] = {}
        log.info("docker watcher initialised")

    @retry(
        reraise=True,
        stop=stop_after_attempt(NUMBER_OF_ATTEMPS),
        wait=wait_random(min=1, max=MAX_TIME_TO_WAIT_S),
        after=after_log(log, logging.DEBUG),
    )
    async def check_for_changes(self) -> dict:
        changes = {}
        with docker_client(self.private_registries) as client:
            for docker_image in self.watched_docker_images:
                try:
                    registry_data = client.images.get_registry_data(
                        docker_image["image"]
                    )
                    if (
                        docker_image["registry_data_attrs"].get("Descriptor")
                        != registry_data.attrs["Descriptor"]
                    ):
                        log.info(
                            "docker image %s signature changed from %s to %s!",
                            docker_image["image"],
                            docker_image["registry_data_attrs"],
                            registry_data.attrs,
                        )
                        changes[
                            docker_image["image"]
                        ] = f"docker image {docker_image['image']} signature changed from {docker_image['registry_data_attrs']} to {registry_data.attrs}"
                except docker.errors.APIError:
                    if docker_image["registry_data_attrs"]:
                        # This means we accessed the docker image from the registry in the past, but now it is not possibly
                        # in that case something is wrong...either docker or config
                        log.exception(
                            "Error while retrieving image %s in registry",
                            docker_image["image"],
                        )
                    else:
                        # in that case the registry does not contain yet the new service
                        log.warning(
                            "Docker image %s is still not available in the registry",
                            docker_image["image"],
                        )
        return changes

    async def cleanup(self):
        pass


__all__: tuple[str, ...] = ("DockerRegistriesWatcher",)
