# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

import asyncio
from collections.abc import AsyncIterator

import pytest
from aiohttp import ClientSession
from yarl import URL

from simcore_service_deployment_agent import portainer


@pytest.fixture
async def aiohttp_client_session() -> AsyncIterator[ClientSession]:
    async with ClientSession() as client:
        yield client


async def test_portainer_connection(
    event_loop: asyncio.AbstractEventLoop,
    portainer_container: tuple[URL, str],
    aiohttp_client_session: ClientSession,
):
    portainer_url, portainer_password = portainer_container

    await portainer.authenticate(
        portainer_url, aiohttp_client_session, "admin", portainer_password
    )
