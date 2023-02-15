# pylint: disable=wildcard-import
# pylint: disable=unused-import
# pylint: disable=unused-variable
# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=bare-except

from typing import Any
from unittest.mock import call

import pytest

import docker
from simcore_service_deployment_agent import docker_registries_watcher
from simcore_service_deployment_agent.docker_registries_watcher import (
    DockerRegistriesWatcher,
)


def _assert_docker_client_calls(
    mocked_docker_client, registry_config: dict[str, Any], docker_stack: dict[str, Any]
):
    mocked_docker_client.assert_has_calls(
        [
            call(),
            call().ping(),
            call().login(
                registry=registry_config["url"],
                username=registry_config["username"],
                password=registry_config["password"],
            ),
            call().images.get_registry_data(docker_stack["services"]["app"]["image"]),
        ]
    )
    mocked_docker_client.reset_mock()


@pytest.fixture
def mock_docker_client(mocker):
    mocked_docker_package = mocker.patch("docker.from_env", autospec=True)
    mocked_docker_package.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somesignature"
    }

    yield mocked_docker_package


def test_mock_docker_client(loop, mock_docker_client, valid_config: dict[str, Any]):
    registry_config = valid_config["main"]["docker_private_registries"][0]

    client = docker.from_env()
    client.ping()
    client.login(
        registry=registry_config["url"],
        username=registry_config["username"],
        password=registry_config["password"],
    )
    assert client.images.get_registry_data().attrs == {
        "Descriptor": "somesignature"
    }, "issue in mocking docker library"  # pylint: disable=no-value-for-parameter


@pytest.fixture
async def docker_watcher(
    mock_docker_client,
    valid_config: dict[str, Any],
    valid_docker_stack: dict[str, Any],
) -> DockerRegistriesWatcher:
    docker_registries_watcher.NUMBER_OF_ATTEMPS = 1
    docker_registries_watcher.MAX_TIME_TO_WAIT_S = 1
    registry_config = valid_config["main"]["docker_private_registries"][0]

    docker_watcher = DockerRegistriesWatcher(valid_config, valid_docker_stack)
    # initialize it now
    await docker_watcher.init()
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)

    # check there is no change for now
    assert not await docker_watcher.check_for_changes()
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)

    return docker_watcher


async def test_docker_registries_watcher(
    mock_docker_client,
    valid_config: dict[str, Any],
    valid_docker_stack: dict[str, Any],
    docker_watcher: DockerRegistriesWatcher,
):
    # create a change
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature"
    }
    change_result = await docker_watcher.check_for_changes()
    assert change_result == {
        "jenkins:latest": "image signature changed",
        "ubuntu": "image signature changed",
    }
    registry_config = valid_config["main"]["docker_private_registries"][0]
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)


@pytest.fixture
def registry_config(valid_config):
    return valid_config["main"]["docker_private_registries"][0]


async def test_docker_registries_watcher_when_registry_fetch_fails(
    mock_docker_client,
    registry_config: dict[str, Any],
    valid_docker_stack: dict[str, Any],
    docker_watcher: DockerRegistriesWatcher,
):
    # Handle the failure of fetching an image
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature"
    }
    mock_docker_client.return_value.images.get_registry_data.side_effect = (
        docker.errors.APIError("Mocked Error Image cant be fetched")
    )
    change_result = await docker_watcher.check_for_changes()

    assert change_result == {}
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature2"
    }
    mock_docker_client.return_value.images.get_registry_data.side_effect = None
    change_result = await docker_watcher.check_for_changes()
    assert change_result == {
        "jenkins:latest": "image signature changed",
        "ubuntu": "image signature changed",
    }
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)
