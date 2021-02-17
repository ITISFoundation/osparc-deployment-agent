# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

import asyncio
from typing import Tuple

import pytest
from aiohttp import ClientSession
from yarl import URL

from simcore_service_deployment_agent import portainer


@pytest.fixture()
async def aiohttp_client_session() -> ClientSession:
    async with ClientSession() as client:
        yield client


async def test_portainer_connection(
    loop: asyncio.AbstractEventLoop,
    portainer_container: Tuple[URL, str],
    aiohttp_client_session: ClientSession,
):
    portainer_url, portainer_password = portainer_container

    await portainer.authenticate(
        portainer_url, aiohttp_client_session, "admin", portainer_password
    )
