# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

import asyncio
import os
import time
from typing import Tuple

import pytest
from aiohttp import ClientSession
from yarl import URL

from simcore_service_deployment_agent import exceptions, portainer


@pytest.fixture()
async def aiohttp_client_session() -> ClientSession:
    async with ClientSession() as client:
        yield client


@pytest.fixture()
async def portainer_baerer_code(
    loop: asyncio.AbstractEventLoop,
    portainer_container: Tuple[URL, str],
    aiohttp_client_session: ClientSession,
) -> str:
    portainer_url, portainer_password = portainer_container

    return await portainer.authenticate(
        portainer_url, aiohttp_client_session, "admin", portainer_password
    )


@pytest.fixture()
async def portainer_endpoint_id(
    loop: asyncio.AbstractEventLoop,
    portainer_container: Tuple[URL, str],
    aiohttp_client_session: ClientSession,
    portainer_baerer_code: str,
) -> int:
    portainer_url, _ = portainer_container

    endpoint = await portainer.get_first_endpoint_id(
        portainer_url, aiohttp_client_session, portainer_baerer_code
    )
    assert type(endpoint) == int
    return endpoint


# async def test_portainer_uppercase_letters_in_stackname_throws()


async def test_portainer_test_create_stack(
    loop: asyncio.AbstractEventLoop,
    portainer_container: Tuple[URL, str],
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


async def test_portainer_raises_when_stack_already_present(
    loop: asyncio.AbstractEventLoop,
    portainer_container: Tuple[URL, str],
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
    with pytest.raises(exceptions.AutoDeployAgentException):
        new_stack = await portainer.post_new_stack(
            base_url=portainer_url,
            app_session=aiohttp_client_session,
            bearer_code=portainer_baerer_code,
            swarm_id=swarm_id,
            endpoint_id=portainer_endpoint_id,
            stack_name="pytestintegration",
            stack_cfg=valid_docker_stack,
        )
    ## Cleanup for subsequent tests: ...
    stack_id = await portainer.get_current_stack_id(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_name="pytestintegration",
    )
    await portainer.delete_stack(
        base_url=portainer_url,
        app_session=aiohttp_client_session,
        bearer_code=portainer_baerer_code,
        stack_id=int(stack_id),
        endpoint_id=portainer_endpoint_id,
    )
