# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable
# pylint: disable=too-many-arguments
# pylint: disable=protected-access

import asyncio
from collections.abc import Awaitable, Iterator
from pathlib import Path
from typing import Any, Callable

import aioresponses
import pytest
import yaml
from aiohttp.test_utils import TestClient
from aioresponses import aioresponses
from pytest import MonkeyPatch
from pytest_mock import MockerFixture
from tenacity.wait import wait_none

from simcore_service_deployment_agent import auto_deploy_task, portainer
from simcore_service_deployment_agent.app_state import State
from simcore_service_deployment_agent.application import create
from simcore_service_deployment_agent.git_url_watcher import GitUrlWatcher
from simcore_service_deployment_agent.models import ComposeSpecsDict

# Monkeypatch the tenacity wait time https://stackoverflow.com/questions/47906671/python-retry-with-tenacity-disable-wait-for-unittest
portainer._portainer_request.retry.wait = wait_none()


@pytest.fixture
def mocked_docker_registries_watcher(mocker: MockerFixture) -> dict[str, Any]:
    mock_docker_watcher = {
        "init": mocker.patch.object(
            auto_deploy_task.DockerRegistriesWatcher, "init", return_value={}
        ),
        "check_for_changes": mocker.patch.object(
            auto_deploy_task.DockerRegistriesWatcher,
            "check_for_changes",
            return_value={},
        ),
    }
    return mock_docker_watcher


@pytest.fixture
def mocked_git_url_watcher(mocker: MockerFixture) -> dict[str, Any]:
    mock_git_changes = {
        "init": mocker.patch.object(GitUrlWatcher, "init", return_value={}),
        "check_for_changes": mocker.patch.object(
            GitUrlWatcher, "check_for_changes", return_value={}
        ),
    }
    return mock_git_changes


@pytest.fixture(scope="session")
def mock_stack_config() -> ComposeSpecsDict:
    cfg = ComposeSpecsDict(
        **{
            "version": "3.7",
            "services": {
                "fake_service": {"image": "fake_image"},
                "fake_service2": {"image": "fake_image"},
            },
        }
    )
    return cfg


@pytest.fixture
def mocked_stack_file(
    valid_config: dict[str, Any], mock_stack_config: ComposeSpecsDict
) -> Iterator[Path]:
    file_name = Path(valid_config["main"]["docker_stack_recipe"]["stack_file"])
    with file_name.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(mock_stack_config, fp)
    yield file_name
    file_name.unlink()


@pytest.fixture
def client(
    event_loop: asyncio.AbstractEventLoop,
    unused_tcp_port_factory: Callable[[], int],
    aiohttp_client: Callable[..., Awaitable[TestClient]],
    valid_config: dict[str, Any],
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> TestClient:
    # Removes all the log errors
    # mocker.patch(
    #     "simcore_service_deployment_agent.auto_deploy_task.portainer.authenticate",
    #     autospec=True,
    #     return_value="bearercode",
    # )

    # increase the speed to fail
    monkeypatch.setattr(auto_deploy_task, "RETRY_COUNT", 2)
    monkeypatch.setattr(auto_deploy_task, "RETRY_WAIT_SECS", 1)

    app = create(valid_config)
    client = event_loop.run_until_complete(
        aiohttp_client(
            app,
            server_kwargs={
                "port": unused_tcp_port_factory(),
                "host": "localhost",
            },
        )
    )
    return client


def test_client(portainer_service_mock: aioresponses, client: TestClient):
    # check that the client starts/stops correctly
    pass


async def test_wait_for_dependencies_no_portainer_up(client: TestClient):
    assert client.app  # nosec

    # wait for the app to start
    while client.app["state"][auto_deploy_task.TASK_NAME] == State.STARTING:
        await asyncio.sleep(1)
    assert client.app["state"][auto_deploy_task.TASK_NAME] == State.FAILED


async def test_filter_services(
    valid_config: dict[str, Any], valid_docker_stack_file: Path
):
    stack_cfg = auto_deploy_task._filter_services(
        excluded_services=valid_config["main"]["docker_stack_recipe"][
            "excluded_services"
        ],
        excluded_volumes=valid_config["main"]["docker_stack_recipe"][
            "excluded_volumes"
        ],
        stack_file=valid_docker_stack_file,
    )
    assert "app" not in stack_cfg["services"]
    assert "some_volume" not in stack_cfg["volumes"]
    assert "build" not in stack_cfg["services"]["anotherapp"]


async def test_add_parameters(
    valid_config: dict[str, Any], valid_docker_stack: ComposeSpecsDict
):
    stack_cfg = auto_deploy_task.add_parameters(valid_config, valid_docker_stack)
    assert "extra_hosts" in stack_cfg["services"]["app"]
    hosts = stack_cfg["services"]["app"]["extra_hosts"]
    assert "original_host:243.23.23.44" in hosts
    assert "some_test_host:123.43.23.44" in hosts
    assert "another_test_host:332.4.234.12" in hosts

    assert "environment" in stack_cfg["services"]["app"]
    envs = stack_cfg["services"]["app"]["environment"]
    assert "ORIGINAL_ENV" in envs
    assert envs["ORIGINAL_ENV"] == "the original env"
    assert "YET_ANOTHER_ENV" in envs
    assert envs["YET_ANOTHER_ENV"] == "this one is replaced"
    assert "TEST_ENV" in envs
    assert envs["TEST_ENV"] == "some test"
    assert "ANOTHER_TEST_ENV" in envs
    assert envs["ANOTHER_TEST_ENV"] == "some other test"

    assert "extra_hosts" in stack_cfg["services"]["anotherapp"]
    hosts = stack_cfg["services"]["anotherapp"]["extra_hosts"]
    assert "some_test_host:123.43.23.44" in hosts
    assert "another_test_host:332.4.234.12" in hosts
    assert "environment" in stack_cfg["services"]["app"]
    envs = stack_cfg["services"]["app"]["environment"]
    assert "TEST_ENV" in envs
    assert envs["TEST_ENV"] == "some test"
    assert "ANOTHER_TEST_ENV" in envs
    assert envs["ANOTHER_TEST_ENV"] == "some other test"

    assert "image" in stack_cfg["services"]["app"]
    assert "alpine:latest" in stack_cfg["services"]["app"]["image"]
    assert "image" in stack_cfg["services"]["anotherapp"]
    assert "ubuntu" in stack_cfg["services"]["anotherapp"]["image"]


async def test_setup_task(
    mocked_docker_registries_watcher,
    mocked_git_url_watcher,
    mocked_subprocess_utils,
    mocked_stack_file,
    portainer_service_mock: aioresponses,
    mattermost_service_mock: aioresponses,
    client: TestClient,
):
    assert client.app
    assert auto_deploy_task.TASK_NAME in client.app
    await asyncio.sleep(1)
    while client.app["state"][auto_deploy_task.TASK_NAME] == State.STARTING:
        await asyncio.sleep(1)
    assert client.app["state"][auto_deploy_task.TASK_NAME] == State.RUNNING
