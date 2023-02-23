# pylint: disable=wildcard-import
# pylint: disable=unused-import
# pylint: disable=unused-variable
# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=protected-access

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import ClientSession
from aioresponses.core import aioresponses
from faker import Faker
from yarl import URL

from simcore_service_deployment_agent import portainer
from simcore_service_deployment_agent.exceptions import ConfigurationError


@pytest.fixture
async def aiohttp_client_session() -> AsyncIterator[ClientSession]:
    async with ClientSession() as client:
        yield client


@pytest.fixture
def faked_stack_name(faker: Faker) -> str:
    return str(faker.word()).lower()


async def test_first_endpoint_id(
    event_loop: asyncio.AbstractEventLoop,
    valid_config: dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
):
    origin = URL(valid_config["main"]["portainer"][0]["url"])

    enpoint_id = await portainer.get_first_endpoint_id(
        origin, aiohttp_client_session, bearer_code=bearer_code
    )
    assert enpoint_id == 1


async def test_get_swarm_id(
    event_loop: asyncio.AbstractEventLoop,
    valid_config: dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
):
    origin = URL(valid_config["main"]["portainer"][0]["url"])
    swarm_id = await portainer.get_swarm_id(
        origin, aiohttp_client_session, bearer_code=bearer_code, endpoint_id=1
    )
    assert swarm_id == "abajmipo7b4xz5ip2nrla6b11"


async def test_stacks(
    event_loop: asyncio.AbstractEventLoop,
    valid_config: dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
    portainer_stacks: dict[str, Any],
    faked_stack_name: str,
):
    for portainer_cfg in valid_config["main"]["portainer"]:
        origin = URL(portainer_cfg["url"])
        stacks_list = await portainer.get_stacks_list(
            origin, aiohttp_client_session, bearer_code=bearer_code
        )
        assert len(stacks_list) == len(portainer_stacks)
        for stack, cfg_stack in zip(stacks_list, portainer_stacks):
            assert stack["Name"] == cfg_stack["Name"]
            assert stack["Id"] == cfg_stack["Id"]

            current_stack_id = await portainer.get_current_stack_id(
                origin,
                aiohttp_client_session,
                bearer_code=bearer_code,
                stack_name=cfg_stack["Name"],
            )
            assert current_stack_id == cfg_stack["Id"]

        # test for an unknown name
        current_stack_id = await portainer.get_current_stack_id(
            origin,
            aiohttp_client_session,
            bearer_code=bearer_code,
            stack_name=faked_stack_name,
        )
        assert not current_stack_id


async def test_create_stack(
    event_loop: asyncio.AbstractEventLoop,
    valid_config: dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
    portainer_stacks: dict[str, Any],
    valid_docker_stack,
    faked_stack_name: str,
    faker: Faker,
):
    swarm_id = 1
    stack_name = faked_stack_name
    for portainer_cfg in valid_config["main"]["portainer"]:
        origin = URL(portainer_cfg["url"])

        endpoint = 1
        new_stack = await portainer.post_new_stack(
            origin,
            aiohttp_client_session,
            bearer_code=bearer_code,
            swarm_id=swarm_id,
            endpoint_id=endpoint,
            stack_name=stack_name,
            stack_cfg=valid_docker_stack,
        )

        updated_stack = await portainer.update_stack(
            origin,
            aiohttp_client_session,
            bearer_code=bearer_code,
            stack_id=str(faker.pyint(min_value=1)),
            endpoint_id=endpoint,
            stack_cfg=valid_docker_stack,
        )


async def test_create_stack_fails_when_name_contains_uppercase_chars(
    loop: asyncio.AbstractEventLoop,
    valid_config: dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
    portainer_stacks: dict[str, Any],
    valid_docker_stack,
):
    swarm_id = 1
    stack_name = "myAmazingstackname"
    for portainer_cfg in valid_config["main"]["portainer"]:
        origin = URL(portainer_cfg["url"])
        endpoint = 1
        with pytest.raises(ConfigurationError):
            new_stack = await portainer.post_new_stack(
                origin,
                aiohttp_client_session,
                bearer_code=bearer_code,
                swarm_id=swarm_id,
                endpoint_id=endpoint,
                stack_name=stack_name,
                stack_cfg=valid_docker_stack,
            )
