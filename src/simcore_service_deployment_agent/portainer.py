import asyncio
import json
import logging
from typing import Optional

from aiohttp import ClientSession, ClientTimeout
from servicelib.logging_utils import log_context
from tenacity import retry
from tenacity.before_sleep import before_sleep_log
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed, wait_random
from yarl import URL

from .exceptions import AutoDeployAgentException, ConfigurationError
from .models import ComposeSpecsDict

log = logging.getLogger(__name__)

NUMBER_OF_ATTEMPS = 5
MAX_TIME_TO_WAIT_S = 10


@retry(
    stop=stop_after_attempt(NUMBER_OF_ATTEMPS),
    wait=wait_fixed(3) + wait_random(0, MAX_TIME_TO_WAIT_S),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
async def _portainer_request(
    url: URL, app_session: ClientSession, method: str, **kwargs
) -> list[dict]:
    attribute = getattr(app_session, method.lower())
    async with attribute(
        url,
        timeout=ClientTimeout(
            total=60, connect=None, sock_connect=None, sock_read=None
        ),
        **kwargs,
    ) as resp:
        log.debug("request received with code %s", resp.status)
        if resp.status == 200:
            data: list[dict] = await resp.json()
            return data
        if resp.status == 204:
            return [{"content": ""}]
        if resp.status == 404:
            log.error("could not find route in %s", url)
            raise ConfigurationError(
                "Could not reach Portainer app in {}:\n {}".format(
                    url, await resp.text()
                )
            )
        log.error("Unknown error")
        raise AutoDeployAgentException(
            "Unknown error ({}) while accessing Portainer app in {}:\n {}".format(
                f"{resp.status}", url, await resp.text()
            )
        )


async def authenticate(
    base_url: URL, app_session: ClientSession, username: str, password: str
) -> str:
    log.debug("authenticating with portainer %s", base_url)
    data: dict[any, any] = await _portainer_request(
        base_url.with_path("api/auth"),
        app_session,
        "POST",
        json={"Username": username, "Password": password},
    )
    bearer_code: str = str(data["jwt"])
    log.debug("authenticated with portainer in %s", base_url)
    return bearer_code


async def get_first_endpoint_id(
    base_url: URL, app_session: ClientSession, bearer_code: str
) -> int:
    log.debug("getting first endpoint id %s", base_url)
    headers = {"Authorization": f"Bearer {bearer_code}"}
    url: URL = base_url.with_path("api/endpoints")
    data = await _portainer_request(url, app_session, "GET", headers=headers)
    log.debug("received list of endpoints: %s", data)
    if not data:
        raise ConfigurationError("portainer does not provide any endpoint")
    return int(data[0]["Id"])


async def get_swarm_id(
    base_url: URL, app_session: ClientSession, bearer_code: str, endpoint_id: int
) -> str:
    log.debug("getting swarm id %s", base_url)
    headers = {"Authorization": f"Bearer {bearer_code}"}
    if endpoint_id < 0:
        endpoint_id = await get_first_endpoint_id(base_url, app_session, bearer_code)
    url: URL = base_url.with_path(f"api/endpoints/{endpoint_id}/docker/swarm")
    data: list[dict] = await _portainer_request(
        url, app_session, "GET", headers=headers
    )
    log.debug("received swarm details: %s", data)
    swarm_id: str = data["ID"]
    return swarm_id


async def get_stacks_list(
    base_url: URL, app_session: ClientSession, bearer_code: str
) -> list[dict]:
    log.debug("getting stacks list %s", base_url)
    headers = {"Authorization": f"Bearer {bearer_code}"}
    url = base_url.with_path("api/stacks")
    data = await _portainer_request(url, app_session, "GET", headers=headers)
    log.debug("received list of stacks: %s", data)
    return data


async def get_current_stack_id(
    base_url: URL, app_session: ClientSession, bearer_code: str, stack_name: str
) -> Optional[int]:  # pylint: disable=unsubscriptable-object
    if stack_name.lower() != stack_name:
        raise ConfigurationError("Docker swarm stack names must be lowercase only!")
    log.debug("getting current stack id %s", base_url)
    stacks_list: list[dict] = await get_stacks_list(base_url, app_session, bearer_code)
    for stack in stacks_list:
        # Portainer / Swarm stacks absolutely need to be lowercase only strings
        if stack_name.lower() == stack["Name"].lower():
            return int(stack["Id"])
    return None


async def post_new_stack(
    base_url: URL,
    app_session: ClientSession,
    bearer_code: str,
    swarm_id: str,
    endpoint_id: int,
    stack_name: str,
    stack_cfg: ComposeSpecsDict,
):  # pylint: disable=too-many-arguments
    if stack_name.lower() != stack_name:
        raise ConfigurationError("Docker swarm stack names must be lowercase only!")
    log.debug("creating new stack %s", base_url)
    if endpoint_id < 0:
        endpoint_id = await get_first_endpoint_id(base_url, app_session, bearer_code)
        log.debug("Determined the following endpoint id: %i", endpoint_id)
    headers = {"Authorization": f"Bearer {bearer_code}"}
    body_data = {
        "Name": stack_name,
        "SwarmID": swarm_id,
        "StackFileContent": json.dumps(stack_cfg, indent=2),
    }
    url = base_url.with_path("api/stacks").with_query(
        {"type": 1, "method": "string", "endpointId": endpoint_id}
    )
    log.debug("Assuming URL:  %s", url)
    log.debug("Assuming headers:")
    log.debug(json.dumps(headers, indent=2))
    log.debug("Assuming data:")
    log.debug(json.dumps(body_data, indent=2))
    log.debug("Sending POST request....")
    data = await _portainer_request(
        url, app_session, "POST", headers=headers, json=body_data
    )
    log.debug("created new stack: %s", data)


async def update_stack(
    base_url: URL,
    app_session: ClientSession,
    bearer_code: str,
    stack_id: int,
    endpoint_id: int,
    stack_cfg: ComposeSpecsDict,
):  # pylint: disable=too-many-arguments
    log.debug("updating stack %s", base_url)
    if endpoint_id < 0:
        endpoint_id = await get_first_endpoint_id(base_url, app_session, bearer_code)
        log.debug("Determined the following endpoint id: %i", endpoint_id)
    headers = {"Authorization": f"Bearer {bearer_code}"}
    body_data = {"StackFileContent": json.dumps(stack_cfg, indent=2), "pullImage": True}
    log.debug("StackFileContent:")
    log.debug(json.dumps(stack_cfg, indent=2, sort_keys=True))
    url = (
        URL(base_url)
        .with_path(f"api/stacks/{stack_id}")
        .with_query({"endpointId": endpoint_id, "method": "string", "type": 1})
    )
    try:
        data = await _portainer_request(
            url, app_session, "PUT", headers=headers, json=body_data
        )
        log.debug("updated stack: %s", data)
    except asyncio.exceptions.TimeoutError:
        log.exception("Unhandled error while deleting stack %s", url)


async def delete_stack(
    base_url: URL,
    app_session: ClientSession,
    bearer_code: str,
    stack_id: int,
    endpoint_id: int,
) -> None:  # pylint: disable=too-many-arguments
    with log_context(
        log,
        logging.DEBUG,
        msg=f"deleting stack {base_url}",
    ):
        if endpoint_id < 0:
            endpoint_id = await get_first_endpoint_id(
                base_url, app_session, bearer_code
            )
            log.debug("Determined the following endpoint id: %i", endpoint_id)
        headers = {"Authorization": f"Bearer {bearer_code}"}
        url = (
            URL(base_url)
            .with_path(f"api/stacks/{stack_id}")
            .with_query({"endpointId": endpoint_id})
        )
        try:
            data = await _portainer_request(url, app_session, "DELETE", headers=headers)
            log.debug("deleted stack: %s", data)
        except asyncio.exceptions.TimeoutError:
            log.exception("Unhandled error while deleting stack %s", url)
