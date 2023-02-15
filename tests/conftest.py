# pylint: disable=unused-argument
# pylint: disable=unused-import
# pylint: disable=bare-except
# pylint: disable=redefined-outer-name

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pytest import FixtureRequest

import simcore_service_deployment_agent

## HELPERS
current_dir = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent


## FIXTURES
pytest_plugins = ["fixtures.fixture_portainer"]

## DIRs


@pytest.fixture(scope="session")
def root_dir() -> Path:
    pdir = current_dir.parent
    assert pdir.exists()
    return pdir


@pytest.fixture(scope="session")
def package_dir() -> Path:
    pdir = Path(simcore_service_deployment_agent.__file__).resolve().parent
    assert pdir.exists()
    return pdir


@pytest.fixture(scope="session")
def api_specs_dir(package_dir: Path) -> Path:
    specs_dir = package_dir / "oas3"
    assert specs_dir.exists()
    return specs_dir


@pytest.fixture(scope="session")
def mocks_dir() -> Path:
    mocks_dir = current_dir / "mocks"
    assert mocks_dir.exists()
    return mocks_dir


## FILEs


@pytest.fixture(
    scope="session",
    params=[
        "valid_config.yaml",
        "valid_config_no_notification.yaml",
    ],
)
def valid_config_file(mocks_dir: Path, request: FixtureRequest) -> Path:
    path = mocks_dir / request.param
    assert path.exists()
    return path


@pytest.fixture(scope="session")
def valid_docker_stack_file(mocks_dir: Path) -> Path:
    path = mocks_dir / "valid_docker_stack.yaml"
    assert path.exists()
    return path


## CONFIGs


@pytest.fixture(scope="session")
def valid_config(valid_config_file: Path) -> dict[str, Any]:
    with valid_config_file.open() as fp:
        return yaml.safe_load(fp)


@pytest.fixture(scope="session")
def valid_docker_stack(valid_docker_stack_file: Path) -> dict[str, Any]:
    with valid_docker_stack_file.open() as fp:
        return yaml.safe_load(fp)
