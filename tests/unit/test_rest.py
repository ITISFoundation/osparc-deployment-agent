# pylint:disable=unused-import
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name

import logging
import sys
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from servicelib.aiohttp.application_keys import APP_CONFIG_KEY

from simcore_service_deployment_agent.rest import setup_rest

logging.basicConfig(level=logging.INFO)


# TODO: reduce log from openapi_core loggers


@pytest.fixture(scope="session")
def openapi_path(api_specs_dir: Path) -> Path:
    specs_path = api_specs_dir / "oas3/v0/openapi.yaml"
    assert specs_path.exits()
    return specs_path


@pytest.fixture(scope="session")
def spec_dict(openapi_path: Path):
    with openapi_path.open() as f:
        spec_dict = yaml.safe_load(f)
    return spec_dict


@pytest.fixture
def client(loop, aiohttp_unused_port, aiohttp_client, api_specs_dir):
    app = web.Application()

    server_kwargs = {"port": aiohttp_unused_port(), "host": "localhost"}
    # fake config
    app[APP_CONFIG_KEY] = {
        "main": server_kwargs,
        "rest": {
            "version": "v0",
            "location": str(api_specs_dir / "v0" / "openapi.yaml"),
        },
    }
    # activates only security+restAPI sub-modules
    setup_rest(app, devel=True)

    cli = loop.run_until_complete(aiohttp_client(app, server_kwargs=server_kwargs))
    return cli


# ------------------------------------------


async def test_check_health(client):
    resp = await client.get("/v0/")
    payload = await resp.json()

    assert resp.status == 200, str(payload)
    data, error = tuple(payload.get(k) for k in ("data", "error"))

    assert data
    assert not error

    assert data["name"] == "simcore_service_deployment_agent"
    # server started without background task
    assert data["status"] == "SERVICE_FAILED"


async def test_check_action(client):
    QUERY = "value"
    ACTION = "echo"
    FAKE = {
        "path_value": "one",
        "query_value": "two",
        "body_value": {"a": "foo", "b": "45"},
    }

    resp = await client.post("/v0/check/{}?data={}".format(ACTION, QUERY), json=FAKE)
    payload = await resp.json()
    data, error = tuple(payload.get(k) for k in ("data", "error"))

    assert resp.status == 200, str(payload)
    assert data
    assert not error

    # TODO: validate response against specs

    assert data["path_value"] == ACTION
    assert data["query_value"] == QUERY
    assert data["body_value"] == FAKE
