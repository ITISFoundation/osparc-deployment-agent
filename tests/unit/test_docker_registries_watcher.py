# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=bare-except

from pathlib import Path
from typing import Any, Dict
from unittest.mock import call

import pytest
import yaml

import docker
from simcore_service_deployment_agent import docker_registries_watcher


def _assert_docker_client_calls(
    mocked_docker_client, registry_config: Dict[str, Any], docker_stack: Dict[str, Any]
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


@pytest.fixture()
def mock_docker_client(mocker):
    mocked_docker_package = mocker.patch("docker.from_env", autospec=True)
    mocked_docker_package.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somesignature"
    }

    yield mocked_docker_package


def test_mock_docker_client(loop, mock_docker_client, valid_config: Dict[str, Any]):
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


async def test_docker_registries_watcher(
    loop,
    mock_docker_client,
    valid_config: Dict[str, Any],
    valid_docker_stack: Dict[str, Any],
):
    docker_registries_watcher.NUMBER_OF_ATTEMPS = 1
    docker_registries_watcher.MAX_TIME_TO_WAIT_S = 1
    registry_config = valid_config["main"]["docker_private_registries"][0]
    docker_watcher = docker_registries_watcher.DockerRegistriesWatcher(
        valid_config, valid_docker_stack
    )
    # initialize it now
    await docker_watcher.init()
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)

    # check there is no change for now
    assert not await docker_watcher.check_for_changes()
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)

    # create a change
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature"
    }
    change_result = await docker_watcher.check_for_changes()
    assert change_result == {
        "jenkins:latest": "image signature changed",
        "ubuntu": "image signature changed",
    }
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)

    # Handle the failure of fetching an image
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature2"
    }
    mock_docker_client.return_value.images.get_registry_data.side_effect = (
        docker.errors.APIError("Mocked Error Image cant be fetched")
    )
    change_result = await docker_watcher.check_for_changes()
    assert change_result == {}
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)
    mock_docker_client.return_value.images.get_registry_data.return_value.attrs = {
        "Descriptor": "somenewsignature3"
    }
    mock_docker_client.return_value.images.get_registry_data.side_effect = None
    change_result = await docker_watcher.check_for_changes()
    assert change_result == {
        "jenkins:latest": "image signature changed",
        "ubuntu": "image signature changed",
    }
    _assert_docker_client_calls(mock_docker_client, registry_config, valid_docker_stack)
