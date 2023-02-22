# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

import asyncio
import os
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

import pytest
from aiohttp import ClientSession
from faker import Faker
from yarl import URL

import docker
from simcore_service_deployment_agent import exceptions, portainer

pytest_plugins = [
    "pytest_simcore.docker_registry",
    "pytest_simcore.docker_swarm",
    "pytest_simcore.schemas",
    "pytest_simcore.repository_paths",
    "fixtures.fixture_portainer",
]


@pytest.fixture(scope="session")
def osparc_simcore_root_dir(request) -> Path:
    return Path().cwd() / ".temp" / "osparc-simcore"


def _run_cmd(cmd: str, **kwargs) -> str:
    result = subprocess.run(
        cmd, capture_output=True, check=True, shell=True, encoding="utf-8", **kwargs
    )
    assert result.returncode == 0
    return result.stdout.rstrip() if result.stdout else ""


@pytest.fixture
async def portainer_baerer_code(
    loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
) -> str:
    portainer_url, portainer_password = portainer_container
    received_bearer_code = await portainer.authenticate(
        portainer_url,
        aiohttp_client_session,
        username="admin",
        password=portainer_password,
    )
    return received_bearer_code


@pytest.fixture
async def aiohttp_client_session() -> AsyncIterator[ClientSession]:
    async with ClientSession() as client:
        yield client


async def test_portainer_connection(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
) -> str:
    portainer_url, portainer_password = portainer_container

    return await portainer.authenticate(
        portainer_url, aiohttp_client_session, "admin", portainer_password
    )


@pytest.fixture
async def portainer_endpoint_id(
    loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_baerer_code: str,
) -> int:
    portainer_url, _ = portainer_container

    endpoint = await portainer.get_first_endpoint_id(
        portainer_url, aiohttp_client_session, portainer_baerer_code
    )
    assert type(endpoint) == int
    return endpoint


async def test_portainer_test_create_stack(
    loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_baerer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack,
):
    portainer_url, _ = portainer_container
    try_to_format_url = URL(portainer_url)
    print(try_to_format_url)
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_baerer_code,
        portainer_endpoint_id,
    )
    # Assuring a clean state by deleting any remnants
    os.system("docker stack rm pytestintegration")

    current_stack_name = ("pytestintegration",)
    new_stack = await portainer.post_new_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        swarm_id=swarm_id,
        endpoint_id=portainer_endpoint_id,
        stack_name="pytestintegration",
        stack_cfg=valid_docker_stack,
    )
    time.sleep(2)
    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_name="pytestintegration",
    )
    time.sleep(2)
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_id=int(stack_id),
        endpoint_id=portainer_endpoint_id,
    )


async def test_portainer_redeploys_when_sha_of_tag_in_docker_registry_changed(
    loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_baerer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack_with_local_registry: dict[str, Any],
    docker_registry: str,
    docker_registry_image_injector: Callable,
    faker: Faker,
):
    ### Push image to local registry
    # Note for the future: This boilerplate might also help: https://github.com/docker/docker-py/issues/2104#issuecomment-410802929
    client = docker.from_env()
    img_name = "itisfoundation/sleeper"
    img_tag = "2.1.1"

    # Note DK2023: On my wsl2 machine, the image is pulled as 127.0.0.1:5000/simcore/services/comp/itis/sleeper:2.1.1,
    # which is not the tag I would have expected. I gueuess this might a potential pitfall on other OS or ports
    sleeper_service = docker_registry_image_injector(img_name, img_tag, faker.email())
    # Retag image
    sleeper_image_name = f"{docker_registry}/{sleeper_service['image']['name']}:{sleeper_service['image']['tag']}"
    sleeper_image = client.images.get(sleeper_image_name)
    ###
    portainer_url, _ = portainer_container
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_baerer_code,
        portainer_endpoint_id,
    )
    # Assuring a clean state by deleting any remnants
    current_stack_name = "pytestintegration"

    os.system("docker stack rm " + current_stack_name)

    new_stack = await portainer.post_new_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        swarm_id=swarm_id,
        endpoint_id=portainer_endpoint_id,
        stack_name=current_stack_name,
        stack_cfg=valid_docker_stack_with_local_registry,
    )
    time.sleep(2)
    #
    ### Retag image in local registry
    ###
    ### Rename old iamge
    sleeper_image.tag(f"{docker_registry}/{sleeper_service['image']['name']}:old")
    image = client.images.pull("postgres", tag="alpine3.17")
    new_image_tag = sleeper_image_name
    assert image.tag(new_image_tag) == True
    client.images.push(new_image_tag)
    #

    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_name=current_stack_name,
    )
    # Get sha of currently running container image
    rawContainerImageBefore = _run_cmd(
        "docker inspect $(docker service ps $(docker service ls | grep sleeper | cut -d ' ' -f1) | grep Running | cut -d ' ' -f1) | jq '.[0].Spec.ContainerSpec.Image'"
    )
    # The result of the above command looks like this: "itisfoundation/webserver:master-github-latest@sha256:ef0a6808167b502ad09ffab707c0fe45923a3f6053159060ddc82415dc207dfa"
    containerImageSHABefore = rawContainerImageBefore.split("@")[1]
    # Assert updating the stack works
    updated_stack = await portainer.update_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
        stack_cfg=valid_docker_stack_with_local_registry,
    )
    time.sleep(5)
    # Assert that the container image sha changed, via docker service labels
    rawContainerImageAfter = _run_cmd(
        'docker inspect $(docker service ls | grep sleeper | cut -d " " -f1) | jq ".[0].Spec.TaskTemplate.ContainerSpec.Image"'
    )
    # Note:
    # Alternatively, we could also check the sha of the contianer and assess the container is re-deployed
    # But this takes time ti take affect and would require sleeps or retr ying polycies. So we dont do it for now. The following call can be used for this purpose:
    # rawContainerImageAfter = _run_cmd("docker inspect $(docker service ps $(docker service ls | grep sleeper | cut -d ' ' -f1) | grep Running | cut -d ' ' -f1) | jq '.[0].Spec.ContainerSpec.Image'")

    containerImageSHAAfter = rawContainerImageAfter.split("@")[1]
    assert containerImageSHABefore != containerImageSHAAfter

    ## Cleanup for subsequent tests: ...
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_id=int(stack_id),
        endpoint_id=portainer_endpoint_id,
    )


async def test_portainer_raises_when_stack_already_present_and_can_delete(
    loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_baerer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack,
):
    portainer_url, _ = portainer_container
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_baerer_code,
        portainer_endpoint_id,
    )
    # Assuring a clean state by deleting any remnants
    current_stack_name = "pytestintegration"
    os.system("docker stack rm " + current_stack_name)

    new_stack = await portainer.post_new_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        swarm_id=swarm_id,
        endpoint_id=portainer_endpoint_id,
        stack_name=current_stack_name,
        stack_cfg=valid_docker_stack,
    )
    time.sleep(2)
    with pytest.raises(exceptions.AutoDeployAgentException):
        new_stack = await portainer.post_new_stack(
            base_url=portainer_url,
            app_session=aiohttp_client_session,
            bearer_code=portainer_baerer_code,
            swarm_id=swarm_id,
            endpoint_id=portainer_endpoint_id,
            stack_name=current_stack_name,
            stack_cfg=valid_docker_stack,
        )
    ## Cleanup for subsequent tests: ...
    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_name=current_stack_name,
    )
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_id=int(stack_id),
        endpoint_id=portainer_endpoint_id,
    )
