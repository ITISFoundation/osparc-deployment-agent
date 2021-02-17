import subprocess
from typing import Tuple

import pytest
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random
from yarl import URL


def _run_cmd(cmd: str, **kwargs) -> str:
    result = subprocess.run(
        cmd, capture_output=True, check=True, shell=True, encoding="utf-8", **kwargs
    )
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
    r = requests.get(url)
    assert r.status_code == code


@pytest.fixture(
    scope="module",
    params=[
        "portainer/portainer:1.24.1",
        "portainer/portainer-ce:2.1.1",
        "portainer/portainer-ce:latest",
    ],
)
def portainer_container(request) -> Tuple[URL, str]:
    portainer_image = request.param
    # create a password (https://documentation.portainer.io/v2.0/deploy/cli/)
    password = "adminadmin"
    encrypted_password = _run_cmd(
        f'docker run --rm httpd:2.4-alpine htpasswd -nbB admin {password} | cut -d ":" -f 2'
    )

    _run_cmd(
        f"docker run --detach --init --publish 8000:8000 --publish 9000:9000 --name=portainer --restart=always --volume /var/run/docker.sock:/var/run/docker.sock {portainer_image} --admin-password='{encrypted_password}' --host unix:///var/run/docker.sock"
    )
    url = URL("http://127.0.0.1:9000/")
    _wait_for_instance(url, code=200)
    yield (url, password)

    _run_cmd("docker rm --force portainer")
