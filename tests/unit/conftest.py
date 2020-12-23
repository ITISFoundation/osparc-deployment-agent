# pylint:disable=redefined-outer-name

import re
from typing import Any, Dict

import faker
import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult

from simcore_service_deployment_agent import auto_deploy_task

fake = faker.Faker()


@pytest.fixture(scope="session")
def bearer_code() -> str:
    FAKE_BEARER_CODE = "TheBearerCode"
    return FAKE_BEARER_CODE


from random import randint


@pytest.fixture(scope="session")
def portainer_stacks(valid_config: Dict[str, Any]) -> Dict[str, Any]:

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
            "Name": fake.name(),
            "Type": 1,
            "EndpointID": randint(1, 10),
        },
    ]
    return stacks


@pytest.fixture()
def aioresponse_mocker() -> aioresponses:
    PASSTHROUGH_REQUESTS_PREFIXES = ["http://127.0.0.1", "ws://"]
    with aioresponses(passthrough=PASSTHROUGH_REQUESTS_PREFIXES) as mock:
        yield mock


@pytest.fixture()
async def mattermost_service_mock(
    aioresponse_mocker: aioresponses, valid_config: Dict[str, Any]
) -> aioresponses:
    get_channels_pattern = re.compile(
        rf'{valid_config["main"]["notifications"][0]["url"]}/api/v4/channels/.+'
    )
    aioresponse_mocker.get(
        get_channels_pattern, status=200, payload={"header": "some text in the header"}
    )
    aioresponse_mocker.put(
        get_channels_pattern, status=200, payload={"success": "bravo"}
    )
    aioresponse_mocker.post(
        f'{valid_config["main"]["notifications"][0]["url"]}/api/v4/posts',
        status=201,
        payload={"success": "bravo"},
    )

    yield aioresponse_mocker


@pytest.fixture()
async def portainer_service_mock(
    aioresponse_mocker: aioresponses,
    bearer_code: str,
    portainer_stacks: Dict[str, Any],
    valid_config: Dict[str, Any],
) -> aioresponses:
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


@pytest.fixture()
def mocked_cmd_utils(mocker):
    mock_run_cmd_line = mocker.patch.object(
        auto_deploy_task,
        "run_cmd_line",
        return_value="",
    )
    yield mock_run_cmd_line
