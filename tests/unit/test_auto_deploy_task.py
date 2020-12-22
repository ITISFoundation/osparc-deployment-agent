# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=bare-except

import asyncio
from asyncio import Future
from pathlib import Path
from typing import Any, Callable, Dict

import aioresponses
import pytest
import yaml
from aioresponses import aioresponses
from pytest_aiohttp import TestClient

from simcore_service_deployment_agent import auto_deploy_task
from simcore_service_deployment_agent.app_state import State
from simcore_service_deployment_agent.application import create
from simcore_service_deployment_agent.git_url_watcher import GitUrlWatcher


@pytest.fixture()
def mocked_docker_registries_watcher(mocker) -> Dict[str, Any]:
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


@pytest.fixture()
def mocked_git_url_watcher(mocker) -> Dict[str, Any]:
    mock_git_changes = {
        "init": mocker.patch.object(GitUrlWatcher, "init", return_value={}),
        "check_for_changes": mocker.patch.object(
            GitUrlWatcher, "check_for_changes", return_value={}
        ),
    }


@pytest.fixture()
def mocked_cmd_utils(mocker):
    mock_run_cmd_line = mocker.patch.object(
        auto_deploy_task,
        "run_cmd_line",
        return_value="",
    )


@pytest.fixture(scope="session")
def mock_stack_config() -> Dict[str, Any]:
    cfg = {
        "version": "3.7",
        "services": {
            "fake_service": {"image": "fake_image"},
            "fake_service2": {"image": "fake_image"},
        },
    }
    return cfg


@pytest.fixture()
def mocked_stack_file(
    valid_config: Dict[str, Any], mock_stack_config: Dict[str, Any]
) -> Path:
    file_name = Path(valid_config["main"]["docker_stack_recipe"]["stack_file"])
    with file_name.open("w") as fp:
        yaml.safe_dump(mock_stack_config, fp)
    yield file_name
    file_name.unlink()


@pytest.fixture
def client(
    loop: asyncio.AbstractEventLoop,
    aiohttp_unused_port: Callable[[], int],
    aiohttp_client: TestClient,
    valid_config: Dict[str, Any],
    monkeypatch,
) -> TestClient:
    # increase the speed to fail
    monkeypatch.setattr(auto_deploy_task, "RETRY_COUNT", 2)
    monkeypatch.setattr(auto_deploy_task, "RETRY_WAIT_SECS", 1)

    app = create(valid_config)
    # app = web.Application()
    # app[APP_CONFIG_KEY] = test_config
    server_kwargs = {"port": aiohttp_unused_port(), "host": "localhost"}

    # auto_deploy_task.setup(app)

    client = loop.run_until_complete(aiohttp_client(app, server_kwargs=server_kwargs))
    yield client


def test_client(portainer_service_mock: aioresponses, client: TestClient):
    # check that the client starts/stops correctly
    pass


async def test_wait_for_dependencies_no_portainer_up(client: TestClient):
    # wait for the app to start
    while client.app["state"][auto_deploy_task.TASK_NAME] == State.STARTING:
        await asyncio.sleep(1)
    assert client.app["state"][auto_deploy_task.TASK_NAME] == State.FAILED


async def test_filter_services(
    valid_config: Dict[str, Any], valid_docker_stack_file: Path
):
    stack_cfg = await auto_deploy_task.filter_services(
        valid_config, valid_docker_stack_file
    )
    assert "app" not in stack_cfg["services"]
    assert "some_volume" not in stack_cfg["volumes"]
    assert "build" not in stack_cfg["services"]["anotherapp"]


async def test_add_parameters(valid_config: Dict[str, Any], valid_docker_stack: Path):
    stack_cfg = await auto_deploy_task.add_parameters(valid_config, valid_docker_stack)
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
    assert "testimage" in stack_cfg["services"]["app"]["image"]
    assert "image" in stack_cfg["services"]["anotherapp"]
    assert "testimage" in stack_cfg["services"]["anotherapp"]["image"]


async def test_setup_task(
    mocked_docker_registries_watcher,
    mocked_git_url_watcher,
    mocked_cmd_utils,
    mocked_stack_file,
    portainer_service_mock: aioresponses,
    client: TestClient,
):
    assert auto_deploy_task.TASK_NAME in client.app
    while client.app["state"][auto_deploy_task.TASK_NAME] == State.STARTING:
        await asyncio.sleep(1)
    assert client.app["state"][auto_deploy_task.TASK_NAME] == State.RUNNING
