import copy
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import attr
from tenacity import retry
from tenacity.after import after_log
from tenacity.before_sleep import before_sleep_log
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed, wait_random
from yarl import URL

from .cmd_utils import run_cmd_line
from .exceptions import ConfigurationError
from .subtask import SubTask

log = logging.getLogger(__name__)

NUMBER_OF_ATTEMPS = 5
MAX_TIME_TO_WAIT_S = 10


@retry(
    stop=stop_after_attempt(NUMBER_OF_ATTEMPS),
    wait=wait_fixed(1) + wait_random(0, MAX_TIME_TO_WAIT_S),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
async def _git_clone_repo(
    repository: URL,
    directory: str,
    branch: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
):
    if username != None and password != None and username != "" and password != "":
        cmd = [
            "git",
            "clone",
            "-n",
            f"{URL(repository).with_user(username).with_password(password)}",
            "--depth",
            "1",
            f"{directory}",
            "--single-branch",
            "--branch",
            branch,
        ]
    else:
        cmd = [
            "git",
            "clone",
            "-n",
            f"{URL(repository)}",
            "--depth",
            "1",
            f"{directory}",
            "--single-branch",
            "--branch",
            branch,
        ]
    await run_cmd_line(cmd)


async def _git_get_FETCH_HEAD_sha(directory: str) -> str:
    cmd = ["git", "rev-parse", "--short", "FETCH_HEAD"]
    sha = await run_cmd_line(cmd, f"{directory}")
    return sha.strip("\n")


async def _git_get_sha_of_tag(directory: str, tag: str) -> str:
    cmd = ["git", "rev-list", "-1", "--sparse", tag]
    sha_long = await run_cmd_line(cmd, f"{directory}")
    cmd = ["git", "rev-parse", "--short", sha_long.strip("\n")]
    sha_short = await run_cmd_line(cmd, f"{directory}")
    return sha_short.strip("\n")


async def _git_clean_repo(directory: str):
    cmd = ["git", "clean", "-dxf"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_checkout_files(directory: str, paths: list[Path], tag: Optional[str]):
    if not tag:
        tag = "HEAD"
    cmd = ["git", "checkout", tag] + [f"{path}" for path in paths]
    await run_cmd_line(cmd, f"{directory}")


async def _git_checkout_repo(directory: str, tag: Optional[str]):
    await _git_checkout_files(directory, [], tag)


async def _git_pull_files(directory: str, paths: list[Path]):
    cmd = ["git", "checkout", "FETCH_HEAD"] + [f"{path}" for path in paths]
    await run_cmd_line(cmd, f"{directory}")


async def _git_pull(directory: str):
    cmd = ["git", "pull"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_fetch(directory: str):
    log.debug("Fetching git repo in %s", f"{directory=}")
    cmd = ["git", "fetch", "--prune", "--tags"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_get_latest_matching_tag(
    directory: str, regexp: str
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    cmd = [
        "git",
        "tag",
        "--list",
        "--sort=creatordate",  # Sorted ascending by date
    ]
    all_tags = await run_cmd_line(cmd, f"{directory}")
    if all_tags == None:
        return None
    all_tags = all_tags.split("\n")
    all_tags = [tag for tag in all_tags if tag != ""]
    list_tags = [tag for tag in all_tags if re.search(regexp, tag) != None]
    return list_tags[-1] if list_tags else None


async def _git_get_current_matching_tag(directory: str, regexp: str) -> list[str]:
    # NOTE: there might be several tags on the same commit
    reg = regexp
    if regexp.startswith("^"):
        reg = regexp[1:]
    cmd = [
        "git",
        "show-ref",
        "--tags",
        "--dereference",
    ]  # | grep --perl-regexp --only-matching "(?<=$(git rev-parse HEAD) refs/tags/){reg}"']
    all_tags = await run_cmd_line(cmd, f"{directory}")
    all_tags = all_tags.split("\n")

    cmd2 = ["git", "rev-parse", "HEAD"]
    shaToBeFound = await run_cmd_line(cmd2, f"{directory}")
    shaToBeFound = shaToBeFound.split("\n")[0]

    associatedTagsFound = []
    for tag in all_tags:
        if shaToBeFound in tag:
            associatedTagsFound.append(tag.split()[-1].split("refs/tags/")[-1])
    foundMatchingTags = []
    for i in associatedTagsFound:
        if re.search(reg, i):
            foundMatchingTags += [i]
    return foundMatchingTags


async def _git_diff_filenames(
    directory: str,
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    cmd = ["git", "--no-pager", "diff", "--name-only", "FETCH_HEAD"]
    modified_files = await run_cmd_line(cmd, f"{directory}")
    return modified_files


async def _git_get_logs(
    directory: str, branch1: str, branch2: str
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    cmd = [
        "git",
        "--no-pager",
        "log",
        "--oneline",
        f"{branch1}..origin/{branch2}",
    ]
    logs = await run_cmd_line(cmd, f"{directory}")
    return logs


async def _git_get_logs_tags(
    directory: str, tag1: str, tag2: str
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    cmd = [
        "git",
        "--no-pager",
        "log",
        "--oneline",
        f"{tag1}..{tag2 if tag1 else tag2}",
    ]
    logs = await run_cmd_line(cmd, f"{directory}")
    return logs


watched_repos = []


@attr.s(auto_attribs=True)
class GitRepo:  # pylint: disable=too-many-instance-attributes, too-many-arguments
    repo_id: str
    repo_url: URL
    branch: str
    tags: str
    username: str
    password: str
    paths: list[Path]
    pull_only_files: bool
    directory: str = ""


async def _checkout_repository(repo: GitRepo, tag: Optional[str] = None):
    if repo.pull_only_files:
        await _git_checkout_files(repo.directory, repo.paths, tag)
    else:
        await _git_checkout_repo(repo.directory, tag)


async def _update_repository(repo: GitRepo):
    if repo.pull_only_files:
        await _git_pull_files(repo.directory, repo.paths)
    else:
        await _git_pull(repo.directory)


async def _init_repositories(repos: list[GitRepo]) -> dict:
    description = {}
    for repo in repos:
        directoryName = tempfile.mkdtemp()
        repo.directory = copy.deepcopy(directoryName)
        log.debug("cloning %s in %s...", repo.repo_id, repo.directory)
        await _git_clone_repo(
            repo.repo_url, repo.directory, repo.branch, repo.username, repo.password
        )
        await _git_fetch(repo.directory)
        latest_tag = (
            await _git_get_latest_matching_tag(repo.directory, repo.tags)
            if repo.tags
            else None
        )
        log.debug(
            "latest tag found for %s is %s, now checking out...",
            repo.repo_id,
            latest_tag,
        )
        if not latest_tag and repo.tags:
            raise ConfigurationError(
                msg=f"no tags found in {repo.repo_url}:{repo.branch} that follows defined tags pattern {repo.tags}: {latest_tag}"
            )
        # This next call was introcued to fix a bug. It is necessary since calls to *_checkout_repository*
        # may only check out files at certain tags while HEAD stays at origin/master.
        # If HEAD!=latest_tag, subsequent calls to *_git_get_current_matching_tag*
        # will return an empty list since HEAD==origin/master is not tagged. This will make the deployment agent fail.
        # I'd call this a workaround and a design deficiency (DK Nov2022)
        # See github.com/ITISFoundation/osparc-deployment-agent/issues/118
        await _git_checkout_repo(repo.directory, latest_tag)
        # This subsequent call, if repo.pull_only_files==true, will only checkout the specified files at the given revision
        await _checkout_repository(repo, latest_tag)
        #
        #
        #
        log.info("repository %s checked out on %s", repo, latest_tag)
        # If no tag: fetch head
        # if tag: sha of tag
        if repo.tags and latest_tag:
            sha = await _git_get_sha_of_tag(repo.directory, latest_tag)
        else:
            sha = await _git_get_FETCH_HEAD_sha(repo.directory)
        log.debug("sha for %s is %s", repo.repo_id, sha)
        description[repo.repo_id] = (
            f"{repo.repo_id}:{repo.branch}:{latest_tag}:{sha}"
            if latest_tag
            else f"{repo.repo_id}:{repo.branch}:{sha}"
        )
    return description


async def _update_repo_using_tags(
    repo: GitRepo,
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    log.debug("checking %s using tags", repo.repo_id)
    # check if current tag is the latest and greatest
    list_current_tags = await _git_get_current_matching_tag(repo.directory, repo.tags)
    latest_tag = await _git_get_latest_matching_tag(repo.directory, repo.tags)
    # there should always be a tag
    if not latest_tag:
        raise ConfigurationError(
            msg=f"no tags found in {repo.repo_id} that follows defined tags pattern {repo.tags}"
        )

    log.debug(
        "following tags found for %s, current: %s, latest: %s",
        repo.repo_id,
        list_current_tags,
        latest_tag,
    )
    if latest_tag in list_current_tags:
        log.debug("no change detected")
        return
    log.info("New tag detected: %s", latest_tag)

    # get modifications
    logged_changes = await _git_get_logs_tags(
        repo.directory, list_current_tags[0], latest_tag
    )
    log.debug("%s tag changes: %s", latest_tag, logged_changes)

    # checkout
    await _checkout_repository(repo, latest_tag)
    log.info("New tag %s  checked out", latest_tag)

    # if the tag changed, an update is needed even if no files were changed
    sha = await _git_get_sha_of_tag(repo.directory, latest_tag)
    return f"{repo.repo_id}:{repo.branch}:{latest_tag}:{sha}"


async def _update_repo_using_branch_head(
    repo: GitRepo,
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    modified_files = await _git_diff_filenames(repo.directory)
    if not modified_files:
        # no modifications
        return
    # get the logs
    logged_changes = await _git_get_logs(repo.directory, repo.branch, repo.branch)
    log.debug("Changelog:\n%s", logged_changes)
    await _update_repository(repo)
    # check if a watched file has changed
    modified_files = modified_files.split()
    common_files = (
        set(modified_files).intersection(set(repo.paths))
        if repo.paths
        else modified_files
    )
    if not common_files:
        # no change affected the watched files
        return

    log.info("File %s changed!!", common_files)
    sha = await _git_get_FETCH_HEAD_sha(repo.directory)
    return f"{repo.repo_id}:{repo.branch}:{sha}"


async def _check_repositories(repos: list[GitRepo]) -> dict:
    changes = {}
    for repo in repos:
        log.debug("checking repo: %s...", repo.repo_url)
        assert repo.directory
        await _git_fetch(repo.directory)
        await _git_clean_repo(repo.directory)

        repo_changes = (
            await _update_repo_using_tags(repo)
            if repo.tags
            else await _update_repo_using_branch_head(repo)
        )
        if repo_changes:
            changes[repo.repo_id] = repo_changes

    return changes


async def _delete_repositories(repos: list[GitRepo]):
    for repo in repos:
        shutil.rmtree(repo.directory, ignore_errors=True)


class GitUrlWatcher(SubTask):
    def __init__(self, app_config: dict):
        super().__init__(name="git repo watcher")
        self.watched_repos = []
        watched_compose_files_config = app_config["main"]["watched_git_repositories"]
        for config in watched_compose_files_config:
            repo = GitRepo(
                repo_id=config["id"],
                repo_url=config["url"],
                branch=config["branch"],
                tags=config["tags"],
                pull_only_files=config["pull_only_files"],
                username=config["username"],
                password=config["password"],
                paths=config["paths"],
            )
            self.watched_repos.append(repo)

    async def init(self) -> dict:
        description = await _init_repositories(self.watched_repos)
        return description

    @retry(
        reraise=True,
        stop=stop_after_attempt(NUMBER_OF_ATTEMPS),
        wait=wait_random(min=1, max=MAX_TIME_TO_WAIT_S),
        after=after_log(log, logging.DEBUG),
    )
    async def check_for_changes(self) -> dict:
        return await _check_repositories(self.watched_repos)

    async def cleanup(self):
        await _delete_repositories(self.watched_repos)


__all__: tuple[str, ...] = ("GitUrlWatcher",)
