""" Basic diagnostic handles to the rest API for operations


"""
import logging

from aiohttp import web
from servicelib.aiohttp.rest_responses import wrap_as_envelope
from servicelib.aiohttp.rest_utils import body_to_dict, extract_and_validate

from . import __version__
from .app_state import State
from .auto_deploy_task import TASK_NAME

log = logging.getLogger(__name__)


async def check_health(request: web.Request):
    params, query, body = await extract_and_validate(request)
    app = request.app

    assert not params
    assert not query
    assert not body
    app_state = app.get("state", {TASK_NAME: State.FAILED})
    data = {
        "name": __name__.split(".")[0],
        "version": __version__,
        "status": f"SERVICE_{app_state[TASK_NAME].name}",
        "api_version": __version__,
    }

    return data


async def check_action(request: web.Request):
    params, query, body = await extract_and_validate(request)

    assert params, "params %s" % params
    assert query, "query %s" % query
    assert body, "body %s" % body

    if params["action"] == "fail":
        raise ValueError("some randome failure")

    # echo's input
    data = {
        "path_value": params.get("action"),
        "query_value": query.get("data"),
        "body_value": body_to_dict(body),
    }

    return wrap_as_envelope(data=data)
