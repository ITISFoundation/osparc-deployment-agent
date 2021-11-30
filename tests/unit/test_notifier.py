# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=protected-access

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import aiohttp
import pytest
import yaml
from aiohttp import web
from aioresponses.core import aioresponses
from yarl import URL

from simcore_service_deployment_agent import notifier


def _list_messages():
    return ["", "some fantastic message"]


@pytest.mark.parametrize("message", _list_messages())
async def test_notify_mattermost(
    loop: asyncio.AbstractEventLoop,
    mattermost_service_mock: aioresponses,
    valid_config: Dict[str, Any],
    message: str,
):
    async def handler(request):
        assert "Authorization" in request.headers
        assert (
            valid_config["main"]["notifications"][0]["personal_token"]
            in request.headers["Authorization"]
        )

        data = await request.json()
        assert "channel_id" in data
        assert (
            data["channel_id"] == valid_config["main"]["notifications"][0]["channel_id"]
        )
        assert "message" in data
        assert valid_config["main"]["notifications"][0]["message"] in data["message"]
        if message:
            assert message in data["message"]
            assert data["message"] == "{}\n{}".format(
                valid_config["main"]["notifications"][0]["message"], message
            )
        else:
            assert (
                data["message"] == valid_config["main"]["notifications"][0]["message"]
            )
        return web.json_response("message_sent", status=201)

    if "notifications" in valid_config["main"]:
        origin = valid_config["main"]["notifications"][0]["url"]
        async with aiohttp.ClientSession() as session:
            await notifier.notify(valid_config, session, message)
