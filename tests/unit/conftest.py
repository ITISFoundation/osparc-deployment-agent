# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable
# pylint: disable=too-many-arguments

import re
from collections.abc import Iterator
from random import randint
from typing import Any
from unittest.mock import MagicMock

import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult
from faker import Faker
from pytest_mock import MockerFixture

from simcore_service_deployment_agent import auto_deploy_task


@pytest.fixture(scope="session")
def bearer_code() -> str:
    FAKE_BEARER_CODE = "TheBearerCode"
    return FAKE_BEARER_CODE


@pytest.fixture
def portainer_stacks(
    valid_config: dict[str, Any], faker: Faker
) -> list[dict[str, Any]]:
    stacks = [
        # some of the Portainer API fields here
        {
            "Id": randint(1, 10),
            "Name": valid_config["main"]["portainer"][0]["stack_name"],
            "Type": 1,
            "EndpointID": randint(1, 10),
        },
        {
            "Id": randint(1, 10),
            "Name": fake.name().replace(" ", "").lower(),
            "Type": 1,
            "EndpointID": randint(1, 10),
        },
    ]
    return stacks


@pytest.fixture
def aioresponse_mocker() -> Iterator[aioresponses]:
    PASSTHROUGH_REQUESTS_PREFIXES = ["http://127.0.0.1", "ws://"]
    with aioresponses(passthrough=PASSTHROUGH_REQUESTS_PREFIXES) as mock:
        yield mock


@pytest.fixture
def mattermost_service_mock(
    aioresponse_mocker: aioresponses, valid_config: dict[str, Any]
) -> Iterator[aioresponses]:
    get_channels_pattern = (
        re.compile(
            rf'{valid_config["main"]["notifications"][0]["url"]}/api/v4/channels/.+'
        )
        if "notifications" in valid_config["main"]
        else re.compile(".*")
    )
    aioresponse_mocker.get(
        get_channels_pattern, status=200, payload={"header": "some text in the header"}
    )
    aioresponse_mocker.put(
        get_channels_pattern, status=200, payload={"success": "bravo"}
    )
    aioresponse_mocker.post(
        f'{valid_config["main"]["notifications"][0]["url"]}/api/v4/posts'
        if "notifications" in valid_config["main"]
        else "...",
        status=201,
        payload={"success": "bravo"},
    )

    yield aioresponse_mocker


@pytest.fixture
def portainer_service_mock(
    aioresponse_mocker: aioresponses,
    bearer_code: str,
    portainer_stacks: dict[str, Any],
    valid_config: dict[str, Any],
) -> Iterator[aioresponses]:
    def _check_auth(**kwargs) -> bool:
        return (
            "headers" in kwargs
            and "Authorization" in kwargs["headers"]
            and f"Bearer {bearer_code}" in kwargs["headers"]["Authorization"]
        )

    def get_stacks_cb(url, **kwargs) -> CallbackResult:
        if not _check_auth(**kwargs):
            return CallbackResult(status=401)

        return CallbackResult(
            status=200,
            payload=portainer_stacks,
        )

    def create_stack_cb(url, **kwargs) -> CallbackResult:
        if not _check_auth(**kwargs):
            return CallbackResult(status=401)

        if "json" not in kwargs:
            return CallbackResult(status=400)
        body = kwargs["json"]

        return CallbackResult(
            status=200,
            payload={
                "SwarmID": body["SwarmID"],
                "Name": body["Name"],
                "EndpointID": url.query["endpointId"],
                "Type": url.query["type"],
                "Id": randint(1, 10),
            },
        )

    def get_endpoints_cb(url, **kwargs) -> CallbackResult:
        if not _check_auth(**kwargs):
            return CallbackResult(status=401)

        return CallbackResult(
            status=200,
            payload=[
                {"Name": valid_config["main"]["portainer"][0]["stack_name"], "Id": 1}
            ],
        )

    def get_docker_swarm_cb(url, **kwargs) -> CallbackResult:
        if not _check_auth(**kwargs):
            return CallbackResult(status=401)
        # returns the docker API /swarm endpoint
        return CallbackResult(
            status=200,
            payload={"ID": "abajmipo7b4xz5ip2nrla6b11"},
        )

    post_authenticate_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/auth")
    get_endpoints_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/endpoints")
    get_swarm_id_pattern = re.compile(
        r"http://[a-z\-0-9_]+:[0-9]+/api/endpoints/[0-9]+/docker/swarm"
    )
    get_stacks_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/stacks")
    create_stack_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/stacks")
    update_stack_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/stacks/[0-9]+")

    aioresponse_mocker.post(
        post_authenticate_pattern,
        status=200,
        payload={"jwt": bearer_code},
        repeat=True,
    )
    aioresponse_mocker.get(
        get_swarm_id_pattern, callback=get_docker_swarm_cb, repeat=True
    )
    aioresponse_mocker.get(
        get_endpoints_pattern, callback=get_endpoints_cb, repeat=True
    )

    aioresponse_mocker.put(update_stack_pattern, status=200, repeat=True)
    aioresponse_mocker.post(create_stack_pattern, callback=create_stack_cb, repeat=True)
    aioresponse_mocker.get(get_stacks_pattern, callback=get_stacks_cb, repeat=True)

    yield aioresponse_mocker


@pytest.fixture
def mocked_cmd_utils(mocker: MockerFixture) -> MagicMock:
    mock_run_cmd_line = mocker.patch.object(
        auto_deploy_task, "run_cmd_line_unsafe", return_value="", autospec=True
    )
    return mock_run_cmd_line
