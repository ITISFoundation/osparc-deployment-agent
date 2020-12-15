# pylint: disable=unused-argument
# pylint: disable=unused-import
# pylint: disable=bare-except
# pylint:disable=redefined-outer-name

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

import simcore_service_deployment_agent


@pytest.fixture(scope='session')
def here() -> Path:
    return Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent


@pytest.fixture(scope='session')
def package_dir(here: Path) -> Path:
    dirpath = Path(simcore_service_deployment_agent.__file__).resolve().parent
    assert dirpath.exists()
    return dirpath


@pytest.fixture(scope='session')
def api_specs_dir(package_dir: Path):
    specs_dir = package_dir / "oas3"
    assert specs_dir.exists()
    return specs_dir


@pytest.fixture
def test_config_file(here: Path) -> Path:
    return Path(here / "test-config.yaml")

@pytest.fixture
def test_config(test_config_file: Path) -> Dict[str, Any]:
    with test_config_file.open() as fp:
        return yaml.safe_load(fp)


@pytest.fixture
def valid_docker_stack_file(here: Path) -> Path:
    return Path(here / "mocks" / "valid_docker_stack.yaml")


@pytest.fixture
def valid_docker_stack(valid_docker_stack_file: Path) -> Dict[str, Any]:
    with valid_docker_stack_file.open() as fp:
        return yaml.safe_load(fp)


@pytest.fixture
def valid_config(here: Path) -> Dict[str, Any]:
    with Path(here / "mocks" / "valid_config.yaml").open() as fp:
        return yaml.safe_load(fp)
