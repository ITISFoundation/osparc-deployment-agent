# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

import asyncio
import os
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any, Callable

import pytest
from aiohttp import ClientSession
from faker import Faker
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed
from yarl import URL

import docker
from simcore_service_deployment_agent import exceptions, portainer
from simcore_service_deployment_agent.models import ComposeSpecsDict
from simcore_service_deployment_agent.subprocess_utils import run_command

pytest_plugins: list[str] = [
    "pytest_simcore.docker_registry",
    "pytest_simcore.docker_swarm",
    "pytest_simcore.schemas",
    "pytest_simcore.repository_paths",
    "fixtures.fixture_portainer",
]

RETRYING_PARAMETERS: dict[str, Any] = {
    "stop": stop_after_attempt(10),
    "wait": wait_fixed(3),
}


@pytest.fixture(scope="session")
def osparc_simcore_root_dir(
    request: type[pytest.FixtureRequest],
) -> (
    Path
):  # It is necessary to overwrite some pytest-simcore fixtures that assert file-paths
    return Path().cwd() / ".temp" / "osparc-simcore"


@pytest.fixture
def stack_name(faker: Faker) -> str:
    return (
        "pytest" + faker.pystr().lower()
    )  # portainer stack names absolutely need to be lwoer case


@pytest.fixture
def clean_stack(stack_name: str) -> Generator[None, None, None]:
    os.system(
        "docker stack rm " + stack_name
    )  # Assuring a clean state by deleting any remnants
    yield
    os.system(
        "docker stack rm " + stack_name
    )  # Assuring a clean state by deleting any remnants


@pytest.fixture
async def portainer_bearer_code(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
) -> str:
    portainer_url, portainer_password = portainer_container
    received_bearer_code: str = await portainer.authenticate(
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
) -> None:
    portainer_url, portainer_password = portainer_container

    await portainer.authenticate(
        portainer_url, aiohttp_client_session, "admin", portainer_password
    )


@pytest.fixture
async def portainer_endpoint_id(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_bearer_code: str,
) -> int:
    portainer_url, _ = portainer_container

    endpoint: int = await portainer.get_first_endpoint_id(
        portainer_url, aiohttp_client_session, portainer_bearer_code
    )
    assert type(endpoint) == int
    return endpoint


async def test_portainer_delete_works(
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_bearer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack: ComposeSpecsDict,
    docker_swarm: None,
    stack_name: str,
    clean_stack: Generator[None, None, None],
):
    portainer_url, _ = portainer_container
    ## Assert that formating to URL does not throw:
    try_to_format_url: URL = URL(portainer_url)
    assert try_to_format_url
    #
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_bearer_code,
        portainer_endpoint_id,
    )

    # Wait for the stack to be present
    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            await portainer.post_new_stack(
                base_url=portainer_url,
                app_session=aiohttp_client_session,
                bearer_code=portainer_bearer_code,
                swarm_id=swarm_id,
                endpoint_id=portainer_endpoint_id,
                stack_name=stack_name,
                stack_cfg=valid_docker_stack,
            )

    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_name=stack_name,
    )
    assert stack_id
    #
    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            returnOfCmdCommand = run_command(
                f"docker stack ls | grep {stack_name} | cat"
            )
            assert returnOfCmdCommand != ""
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
    )
    # Check that the swarm is actually gone
    # Wait for the stack to be present
    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            assert run_command(f"docker stack ls | grep {stack_name} | cat") == ""
    # Check that deleting a non-existant stack fails
    with pytest.raises(exceptions.AutoDeployAgentException):
        await portainer.delete_stack(
            base_url=portainer_url,
            app_session=aiohttp_client_session,
            bearer_code=portainer_bearer_code,
            stack_id=stack_id,
            endpoint_id=portainer_endpoint_id,
        )


async def test_portainer_test_create_stack(
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_bearer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack: ComposeSpecsDict,
    docker_swarm: None,
    stack_name,
    clean_stack,
) -> None:
    portainer_url, _ = portainer_container
    ## Assert that formating to URL does not throw:
    try_to_format_url: URL = URL(portainer_url)
    assert try_to_format_url
    #
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_bearer_code,
        portainer_endpoint_id,
    )

    await portainer.post_new_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        swarm_id=swarm_id,
        endpoint_id=portainer_endpoint_id,
        stack_name=stack_name,
        stack_cfg=valid_docker_stack,
    )

    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            await portainer.get_current_stack_id(
                base_url=portainer_url,
                app_session=aiohttp_client_session,
                bearer_code=portainer_bearer_code,
                stack_name=stack_name,
            )
    #
    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_name=stack_name,
    )
    assert stack_id
    ## Cleanup for subsequent tests:
    # This is strictly necessary, even with the clean_stack fixture,
    # As portainer might think stacks still exist when they are deleted using `docker stack rm`
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
    )


async def test_portainer_redeploys_when_sha_of_tag_in_docker_registry_changed(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_bearer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack_with_local_registry: ComposeSpecsDict,
    docker_registry: str,
    docker_registry_image_injector: Callable,
    faker: Faker,
    docker_swarm: None,
    stack_name,
    clean_stack,
):
    ### Push image to local registry
    # Note for the future: This boilerplate might also help: https://github.com/docker/docker-py/issues/2104#issuecomment-410802929
    client = docker.from_env()
    img_name = "itisfoundation/sleeper"
    img_tag = "2.1.1"

    # Note DK2023: On my wsl2 machine, the image is pulled as 127.0.0.1:5000/simcore/services/comp/itis/sleeper:2.1.1,
    sleeper_service = docker_registry_image_injector(img_name, img_tag, faker.email())
    # Retag image
    sleeper_image_name = f"{docker_registry}/{sleeper_service['image']['name']}:{sleeper_service['image']['tag']}"
    sleeper_image = client.images.get(sleeper_image_name)
    ###
    portainer_url, _ = portainer_container
    swarm_id: str = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_bearer_code,
        portainer_endpoint_id,
    )

    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            await portainer.post_new_stack(
                base_url=portainer_url,
                app_session=aiohttp_client_session,
                bearer_code=portainer_bearer_code,
                swarm_id=swarm_id,
                endpoint_id=portainer_endpoint_id,
                stack_name=stack_name,
                stack_cfg=valid_docker_stack_with_local_registry,
            )

    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            await portainer.get_current_stack_id(
                base_url=portainer_url,
                app_session=aiohttp_client_session,
                bearer_code=portainer_bearer_code,
                stack_name=stack_name,
            )
    #
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
        bearer_code=portainer_bearer_code,
        stack_name=stack_name,
    )
    assert stack_id
    # Get sha of currently running container image
    rawContainerImageBefore = run_command(
        "docker inspect $(docker service ps $(docker service ls | grep sleeper | cut -d ' ' -f1) | grep Running | cut -d ' ' -f1) | jq '.[0].Spec.ContainerSpec.Image'"
    )
    # The result of the above command looks like this: "itisfoundation/webserver:master-github-latest@sha256:ef0a6808167b502ad09ffab707c0fe45923a3f6053159060ddc82415dc207dfa"
    containerImageSHABefore = rawContainerImageBefore.split("@")[1]
    # Assert updating the stack works
    updated_stack = await portainer.update_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
        stack_cfg=valid_docker_stack_with_local_registry,
    )
    # Assert that the container image sha changed, via docker service labels
    rawContainerImageAfter = run_command(
        'docker inspect $(docker service ls | grep sleeper | cut -d " " -f1) | jq ".[0].Spec.TaskTemplate.ContainerSpec.Image"'
    )
    # Note:
    # Alternatively, we could also check the sha of the contianer and assess the container is re-deployed
    # But this takes time ti take affect and would require sleeps or retr ying polycies. So we dont do it for now. The following call can be used for this purpose:
    # rawContainerImageAfter = run_command("docker inspect $(docker service ps $(docker service ls | grep sleeper | cut -d ' ' -f1) | grep Running | cut -d ' ' -f1) | jq '.[0].Spec.ContainerSpec.Image'")

    containerImageSHAAfter = rawContainerImageAfter.split("@")[1]
    assert containerImageSHABefore != containerImageSHAAfter

    ## Cleanup for subsequent tests:
    # This is strictly necessary, even with the clean_stack fixture,
    # As portainer might think stacks still exist when they are deleted using `docker stack rm`
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
    )


async def test_portainer_raises_when_stack_already_present_and_can_delete(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_bearer_code: str,
    portainer_endpoint_id: int,
    valid_docker_stack: ComposeSpecsDict,
    docker_swarm: None,
):
    portainer_url, _ = portainer_container
    swarm_id = await portainer.get_swarm_id(
        portainer_url,
        aiohttp_client_session,
        portainer_bearer_code,
        portainer_endpoint_id,
    )
    # Assuring a clean state by deleting any remnants
    current_stack_name = "pytestintegration"
    os.system("docker stack rm " + current_stack_name)

    new_stack = await portainer.post_new_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        swarm_id=swarm_id,
        endpoint_id=portainer_endpoint_id,
        stack_name=current_stack_name,
        stack_cfg=valid_docker_stack,
    )

    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            await portainer.get_current_stack_id(
                base_url=portainer_url,
                app_session=aiohttp_client_session,
                bearer_code=portainer_bearer_code,
                stack_name=current_stack_name,
            )

    with pytest.raises(exceptions.AutoDeployAgentException):
        new_stack = await portainer.post_new_stack(
            base_url=portainer_url,
            app_session=aiohttp_client_session,
            bearer_code=portainer_bearer_code,
            swarm_id=swarm_id,
            endpoint_id=portainer_endpoint_id,
            stack_name=current_stack_name,
            stack_cfg=valid_docker_stack,
        )
    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_name=current_stack_name,
    )
    assert stack_id
    ## Cleanup for subsequent tests:
    # This is strictly necessary, even with the clean_stack fixture,
    # As portainer might think stacks still exist when they are deleted using `docker stack rm`
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_bearer_code,
        stack_id=stack_id,
        endpoint_id=portainer_endpoint_id,
    )
