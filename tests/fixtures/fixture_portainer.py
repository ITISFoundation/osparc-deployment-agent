import subprocess
from collections.abc import Iterator
from contextlib import suppress

import pytest
import requests
from tenacity import retry
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_random
from yarl import URL


def _run_cmd(cmd: list[str], **kwargs) -> str:
    result = subprocess.run(
        cmd, capture_output=True, check=True, shell=False, encoding="utf-8", **kwargs
    )
    print("ran:")
    print(cmd)
    print("got:")
    print(result.stdout.rstrip())
    print("err:")
    print(result.stderr.rstrip())
    #
    print(result.stderr.rstrip())
    assert result.returncode == 0
    return result.stdout.rstrip() if result.stdout else ""


@retry(
    reraise=True,
    stop=stop_after_attempt(10),
    wait=wait_random(min=1, max=5),
    retry=retry_if_exception_type(
        (AssertionError, requests.exceptions.ConnectionError)
    ),
)
def _wait_for_instance(url: URL, code: int = 200):
    r = requests.get(f"{url}")
    assert r.status_code == code


@pytest.fixture(
    scope="module",
    params=[
        "portainer/portainer:1.24.1",
        "portainer/portainer-ce:2.1.1",
        "portainer/portainer-ce:latest",
    ],
)
def portainer_container(request) -> Iterator[tuple[URL, str]]:
    portainer_image = request.param

    # create a password (https://documentation.portainer.io/v2.0/deploy/cli/)
    password = "adminadmin"
    encrypted_password = _run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "httpd:2.4-alpine",
            "htpasswd",
            "-nbB",
            "admin",
            password,
        ]
    ).split(":")[-1]

    with suppress(Exception):
        _run_cmd(["docker", "rm", "--force", "portainer"])

    _run_cmd(
        [
            "docker",
            "run",
            "--detach",
            "--init",
            "--publish",
            "8000:8000",
            "--publish",
            "9000:9000",
            "--name=portainer",
            "--restart=always",
            "--volume",
            "/var/run/docker.sock:/var/run/docker.sock",
            portainer_image,
            "--admin-password=" + f"{encrypted_password}",
            "--host",
            "unix:///var/run/docker.sock",
        ]
    )
    url = URL("http://127.0.0.1:9000/")
    _wait_for_instance(url, code=200)

    yield (url, password)

    _run_cmd(["docker", "rm", "--force", "portainer"])
