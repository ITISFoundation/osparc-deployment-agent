import re

import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult


@pytest.fixture()
async def portainer_service_mock(valid_config) -> aioresponses:
    PASSTHROUGH_REQUESTS_PREFIXES = ["http://127.0.0.1", "ws://"]
    post_authenticate_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/auth")

    def get_stacks_cb(url, **kwargs) -> CallbackResult:
        assert "headers" in kwargs
        assert "Authorization" in kwargs["headers"]
        assert "Bearer testBearerCode" in kwargs["headers"]["Authorization"]

        return CallbackResult(
            status=200,
            payload=[
                {"Name": valid_config["main"]["portainer"][0]["stack_name"], "Id": 1}
            ],
        )

    get_stacks_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/stacks")

    update_stack_pattern = re.compile(r"http://[a-z\-0-9_]+:[0-9]+/api/stacks/[0-9]+")
    with aioresponses(passthrough=PASSTHROUGH_REQUESTS_PREFIXES) as mock:
        mock.post(
            post_authenticate_pattern,
            status=200,
            payload={"jwt": "testBearerCode"},
            repeat=True,
        )
        mock.get(get_stacks_pattern, callback=get_stacks_cb, repeat=True)
        mock.put(update_stack_pattern, status=200, repeat=True)
        yield mock
