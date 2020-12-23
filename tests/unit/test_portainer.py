# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=protected-access

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from aiohttp import ClientSession, web
from aioresponses.core import aioresponses
from yarl import URL

from simcore_service_deployment_agent import exceptions, portainer


@pytest.fixture()
async def aiohttp_client_session() -> ClientSession:
    async with ClientSession() as client:
        yield client


async def test_authenticate(
    loop: asyncio.AbstractEventLoop,
    valid_config: Dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
):

    origin = URL(valid_config["main"]["portainer"][0]["url"])
    received_bearer_code = await portainer.authenticate(
        origin, aiohttp_client_session, username="testuser", password="password"
    )
    assert received_bearer_code == bearer_code


async def test_first_endpoint_id(
    loop: asyncio.AbstractEventLoop,
    valid_config: Dict[str, Any],
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
    loop: asyncio.AbstractEventLoop,
    valid_config: Dict[str, Any],
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
    loop: asyncio.AbstractEventLoop,
    valid_config: Dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
    portainer_stacks: Dict[str, Any],
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
            stack_name="this is a anknown name",
        )
        assert not current_stack_id


async def test_create_stack(
    loop: asyncio.AbstractEventLoop,
    valid_config: Dict[str, Any],
    portainer_service_mock: aioresponses,
    aiohttp_client_session: ClientSession,
    bearer_code: str,
    portainer_stacks: Dict[str, Any],
    valid_docker_stack,
):
    swarm_id = 1
    stack_name = "my amazing stack name"
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
            stack_id="1",
            endpoint_id=endpoint,
            stack_cfg=valid_docker_stack,
        )
