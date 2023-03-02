import asyncio
import contextlib
import copy
import json
import logging
import tempfile
from asyncio import create_task
from asyncio.exceptions import CancelledError
from contextlib import suppress
from pathlib import Path
from shutil import copy2
from typing import Any

import yaml
from aiohttp import ClientError, ClientSession, web
from aiohttp.client import ClientTimeout
from aiohttp.client_exceptions import ClientConnectorError
from servicelib.aiohttp.application_keys import APP_CONFIG_KEY
from tenacity import retry
from tenacity.before_sleep import before_sleep_log
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed
from yarl import URL

from . import portainer
from .app_state import State
from .cmd_utils import run_cmd_line_unsafe
from .docker_registries_watcher import DockerRegistriesWatcher
from .exceptions import ConfigurationError, DependencyNotReadyError
from .git_url_watcher import GitRepo, GitUrlWatcher, RepoID
from .models import ComposeSpecsDict, ServiceName, VolumeName
from .notifier import notify, notify_state
from .subtask import SubTask

log = logging.getLogger(__name__)

TASK_NAME = f"{__name__}_autodeploy_task"
TASK_SESSION_NAME = f"{__name__}session"

RETRY_WAIT_SECS = 2
RETRY_COUNT = 10


def _filter_services(
    excluded_services: list[ServiceName],
    excluded_volumes: list[VolumeName],
    stack_file: Path,
) -> ComposeSpecsDict:
    log.debug("filtering services and volumes")

    stack_cfg: ComposeSpecsDict = yaml.safe_load(
        Path(stack_file).read_text(encoding="UTF-8")
    )
    # remove excluded services
    for service in excluded_services:
        stack_cfg["services"].pop(service, None)

    # remove excluded volumes
    for volume in excluded_volumes:
        stack_cfg.get("volumes", {}).pop(volume, None)

    # remove build part, useless in a stack
    for service in stack_cfg["services"].keys():
        stack_cfg["services"][service].pop("build", None)

        # Taking care of wrong format in extra_hosts
        if "extra_hosts" in stack_cfg["services"][service]:
            if isinstance(stack_cfg["services"][service]["extra_hosts"], dict):
                if (
                    len(stack_cfg["services"][service]["extra_hosts"].keys()) == 1
                    and "" in stack_cfg["services"][service]["extra_hosts"]
                ):
                    if stack_cfg["services"][service]["extra_hosts"][""] == "":
                        stack_cfg["services"][service]["extra_hosts"] = []

    log.debug(
        "filtered services: result in:\n%s",
        json.dumps(stack_cfg, indent=2, sort_keys=True),
    )
    return stack_cfg


def add_parameters(
    app_config: dict[str, Any], stack_cfg: ComposeSpecsDict
) -> ComposeSpecsDict:
    additional_parameters = app_config["main"]["docker_stack_recipe"][
        "additional_parameters"
    ]
    log.debug("adding parameters to stack using %s", additional_parameters)
    for key, value in additional_parameters.items():
        if value and isinstance(value, dict):
            for service_params in stack_cfg["services"].values():
                if key in service_params:
                    service_params[key].update(value)
                else:
                    service_params[key] = value
        elif value and isinstance(value, list):
            for _, service_params in stack_cfg["services"].items():
                if key in service_params:
                    service_params[key].extend(value)
                else:
                    service_params[key] = value
        elif value and isinstance(value, str):
            for _, service_params in stack_cfg["services"].items():
                service_params[key] = value

    return stack_cfg


def add_prefix_to_services(
    app_config: dict[str, Any], stack_cfg: ComposeSpecsDict
) -> ComposeSpecsDict:
    services_prefix = app_config["main"]["docker_stack_recipe"]["services_prefix"]
    if services_prefix:
        log.debug("adding service prefix %s to all services", services_prefix)
        services = stack_cfg["services"]
        new_services = {}
        for service_name in services.keys():
            new_service_name = f"{services_prefix}_{service_name}"
            new_services[new_service_name] = services[service_name]
        stack_cfg["services"] = new_services
    return stack_cfg


async def generate_stack_file(
    app_config: dict[str, Any], git_task: GitUrlWatcher
) -> Path:
    stack_recipe_cfg = app_config["main"]["docker_stack_recipe"]

    # collect repos informations
    git_repos: dict[RepoID, GitRepo] = {r.repo_id: r for r in git_task.watched_repos}

    # collect files in one location
    dest_dir = stack_recipe_cfg["workdir"]
    if dest_dir == "temp":
        # create a temp folder
        directory_name = tempfile.mkdtemp()  # TODO: use asyncexitstack and aiofiles
        dest_dir = copy.deepcopy(directory_name)

    elif dest_dir in git_repos:
        # we use one of the git repos
        dest_dir = git_repos[dest_dir].directory

    file_groups_to_copy = stack_recipe_cfg["files"]
    for group in file_groups_to_copy:
        git_id = group["id"]
        if not git_id in git_repos:
            raise ConfigurationError(
                "recipe is using an id {} that is not available in the watched git repositories".format(
                    git_id
                )
            )

        src_dir = git_repos[git_id].directory
        files = group["paths"]
        for src_file in files:
            src = Path(src_dir) / Path(src_file)
            if not src.exists():
                raise ConfigurationError(
                    "recipe from id {} uses file non existing file {}".format(
                        git_id, src_file
                    )
                )
            copy2(src, dest_dir)

    # execute command if available
    if stack_recipe_cfg["command"]:
        # The command in the stack_recipe might contain shell natives like pipes and cd
        # Thus we run it in unsafe mode as a proper shell.
        await run_cmd_line_unsafe(stack_recipe_cfg["command"], cwd_=dest_dir)

    stack_file = Path(dest_dir) / Path(stack_recipe_cfg["stack_file"])

    # Filesize check via https://stackoverflow.com/a/55949699
    if not stack_file.exists() or not stack_file.stat().st_size:
        raise ConfigurationError(
            f"The generation of {stack_file=} failed using {stack_recipe_cfg['command']}. The file is non-existant or an empty file."
        )
    return stack_file


async def deploy_stacks(
    app_config: dict[str, Any], app_session: ClientSession, stack_cfg: ComposeSpecsDict
):
    log.debug("updating portainer stack using: %s", stack_cfg)
    portainer_cfg = app_config["main"]["portainer"]
    for config in portainer_cfg:
        url = URL(config["url"])
        bearer_code = await portainer.authenticate(
            url, app_session, config["username"], config["password"]
        )
        current_stack_id = await portainer.get_current_stack_id(
            url, app_session, bearer_code, config["stack_name"]
        )
        if not current_stack_id:
            # stack does not exist
            swarm_id = await portainer.get_swarm_id(
                url, app_session, bearer_code, config["endpoint_id"]
            )
            await portainer.post_new_stack(
                url,
                app_session,
                bearer_code,
                swarm_id,
                config["endpoint_id"],
                config["stack_name"],
                stack_cfg,
            )
        else:
            log.debug("updating the configuration of the stack...")
            await portainer.update_stack(
                url,
                app_session,
                bearer_code,
                current_stack_id,
                config["endpoint_id"],
                stack_cfg,
            )


async def stacks_exist(app_config: dict[str, Any], app_session: ClientSession) -> bool:
    log.debug("checking if portainer stacks exist...")
    portainer_cfg = app_config["main"]["portainer"]
    for config in portainer_cfg:
        url = URL(config["url"])
        bearer_code = await portainer.authenticate(
            url, app_session, config["username"], config["password"]
        )
        current_stack_id = await portainer.get_current_stack_id(
            url, app_session, bearer_code, config["stack_name"]
        )
        if not current_stack_id:
            # stack does not exist
            return False
    return True


async def create_docker_registries_watch_subtask(
    app_config: dict[str, Any], stack_cfg: ComposeSpecsDict
) -> DockerRegistriesWatcher:
    log.debug("creating docker watch subtask")
    docker_subtask = DockerRegistriesWatcher(app_config, stack_cfg)
    await docker_subtask.init()
    return docker_subtask


async def create_git_watch_subtask(
    app_config: dict[str, Any]
) -> tuple[GitUrlWatcher, dict]:
    log.debug("creating git repo watch subtask")
    git_sub_task = GitUrlWatcher(app_config)
    descriptions = await git_sub_task.init()
    return (git_sub_task, descriptions)


async def create_stack(
    git_task: GitUrlWatcher, app_config: dict[str, Any]
) -> ComposeSpecsDict:
    # generate the stack file
    stack_file: Path = await generate_stack_file(app_config, git_task)
    log.debug("generated stack file in %s", stack_file.name)

    # filter the stack file if needed
    stack_cfg: ComposeSpecsDict = _filter_services(
        excluded_services=app_config["main"]["docker_stack_recipe"][
            "excluded_services"
        ],
        excluded_volumes=app_config["main"]["docker_stack_recipe"]["excluded_volumes"],
        stack_file=stack_file,
    )
    log.debug("filtered stack configuration")

    # add parameter to the stack file if needed
    stack_cfg = add_parameters(app_config, stack_cfg)
    log.debug("new stack config is\n%s", stack_file)

    # change services names to avoid conflicts in common networks
    stack_cfg = add_prefix_to_services(app_config, stack_cfg)

    log.debug("final stack compose specs is:")
    log.debug(json.dumps(stack_cfg, indent=4, sort_keys=True))
    return stack_cfg


async def check_changes(subtasks: list[SubTask]) -> dict:
    changes = {}
    for task in subtasks:
        changes.update(await task.check_for_changes())
    return changes


@retry(
    wait=wait_fixed(RETRY_WAIT_SECS),
    stop=stop_after_attempt(RETRY_COUNT),
    before_sleep=before_sleep_log(log, logging.INFO),
    retry=retry_if_exception_type(DependencyNotReadyError),
    reraise=True,
)
async def wait_for_dependencies(app_config: dict[str, Any], app_session: ClientSession):
    log.info("waiting for dependencies to start...")
    # wait for a portainer instance
    portainer_cfg = app_config["main"]["portainer"]
    for config in portainer_cfg:
        url = URL(config["url"])
        try:
            await portainer.authenticate(
                url, app_session, config["username"], config["password"]
            )
            log.info("portainer at %s ready", url)
        except (ClientError, ClientConnectorError) as e:
            log.exception("portainer not ready at %s", url)
            raise DependencyNotReadyError(f"Portainer not ready at {url}") from e


async def _init_deploy(
    app: web.Application,
) -> tuple[GitUrlWatcher, DockerRegistriesWatcher]:
    try:
        log.info("initialising...")
        # get configs
        app["state"][TASK_NAME] = State.STARTING
        app_config = app[APP_CONFIG_KEY]
        app_session = app[TASK_SESSION_NAME]

        # wait for portainer to be available
        await wait_for_dependencies(app_config, app_session)

        # create initial stack
        git_task, descriptions = await create_git_watch_subtask(app_config)
        stack_cfg = await create_stack(git_task, app_config)
        docker_task = await create_docker_registries_watch_subtask(
            app_config, stack_cfg
        )

        # deploy stack to swarm
        await deploy_stacks(app_config, app_session, stack_cfg)

        # notifications
        await notify(
            app_config,
            app_session,
            message=f"Stack initialised with:\n{list(descriptions.values())}",
        )
        main_repo = app_config["main"]["docker_stack_recipe"]["workdir"]
        await notify_state(
            app_config,
            app_session,
            state=app["state"][TASK_NAME],
            message=descriptions[main_repo] if main_repo in descriptions else "",
        )
        log.info("initialisation completed")
        return (git_task, docker_task)

    except asyncio.CancelledError:
        log.info("task cancelled")
        raise


async def _deploy(
    app: web.Application, git_task: GitUrlWatcher, docker_task: DockerRegistriesWatcher
) -> DockerRegistriesWatcher:
    app_config = app[APP_CONFIG_KEY]
    app_session = app[TASK_SESSION_NAME]

    log.info("check if stacks exist...")
    if not await stacks_exist(app_config, app_session):
        log.warning("stacks do not exist, initialising...")
        # notifications
        stack_cfg = await create_stack(git_task, app_config)
        await deploy_stacks(app_config, app_session, stack_cfg)
        await notify(
            app_config,
            app_session,
            message="Stack was not found and re-initialised.",
        )
        main_repo = app_config["main"]["docker_stack_recipe"]["workdir"]
        await notify_state(
            app_config,
            app_session,
            state=app["state"][TASK_NAME],
            message="Stack was not found and re-initialised.",
        )
        log.info("initialisation completed")

    log.info("Checking for changes...")
    changes = await check_changes([git_task, docker_task])
    if not changes:
        log.info("--> no changes detected")
        return docker_task
    log.info("--> changes detected")

    stack_cfg = await create_stack(git_task, app_config)
    docker_task = await create_docker_registries_watch_subtask(app_config, stack_cfg)

    # deploy stack to swarm
    log.info("redeploying the stack...")
    await deploy_stacks(app_config, app_session, stack_cfg)
    log.info("sending notifications...")
    changes_as_texts = [f"{key}:{value}" for key, value in changes.items()]
    await notify(app_config, app_session, message=f"Updated stack\n{changes_as_texts}")
    main_repo = app_config["main"]["docker_stack_recipe"]["workdir"]
    if main_repo in changes:
        await notify_state(
            app_config,
            app_session,
            state=app["state"][TASK_NAME],
            message=changes[main_repo],
        )
    log.info("stack re-deployed")
    return docker_task


async def auto_deploy(app: web.Application):
    log.info("start autodeploy task")
    app_config = app[APP_CONFIG_KEY]
    app_session = app[TASK_SESSION_NAME]
    # init
    try:
        git_task, docker_task = await _init_deploy(app)
    except CancelledError:
        app["state"][TASK_NAME] = State.STOPPED
        return
    except Exception:  # pylint: disable=broad-except
        log.exception("Error while initializing deployment: ")
        # this will trigger a restart from the docker swarm engine
        app["state"][TASK_NAME] = State.FAILED
        return

    # loop forever to detect changes
    while True:
        try:
            app["state"][TASK_NAME] = State.RUNNING
            docker_task = await _deploy(app, git_task, docker_task)
            await asyncio.sleep(app_config["main"]["polling_interval"])

        except asyncio.CancelledError:
            log.info("cancelling task...")
            app["state"][TASK_NAME] = State.STOPPED
            break

        except Exception as exc:  # pylint: disable=broad-except
            # some unknown error happened, let's wait 5 min and restart
            log.exception("Task error:")
            if app["state"][TASK_NAME] != State.PAUSED:
                app["state"][TASK_NAME] = State.PAUSED
                with contextlib.suppress(Exception):
                    await notify_state(
                        app_config,
                        app_session,
                        state=app["state"][TASK_NAME],
                        message=f"{exc}",
                    )
            await asyncio.sleep(300)

        finally:
            # cleanup the subtasks
            log.info("task completed...")


#
# EVENTS
#


async def background_task(app: web.Application):
    app["state"] = {TASK_NAME: State.STARTING}
    app[TASK_NAME] = create_task(auto_deploy(app))
    yield
    task = app[TASK_NAME]
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def persistent_session(app):
    async with ClientSession(timeout=ClientTimeout(5)) as session:
        app[TASK_SESSION_NAME] = session
        yield


#
# API
#


def setup_auto_deploy_task(app: web.Application):
    app.cleanup_ctx.append(persistent_session)
    try:
        app.cleanup_ctx.append(background_task)
    except asyncio.CancelledError:
        print("We Encountered an error in running the deployment agent:")


__all__: tuple[str, ...] = ("setup_auto_deploy_task",)
