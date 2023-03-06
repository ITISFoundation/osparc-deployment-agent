import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiofiles.tempfile import TemporaryDirectory
from servicelib.file_utils import remove_directory
from tenacity import retry
from tenacity.after import after_log
from tenacity.before_sleep import before_sleep_log
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed, wait_random
from yarl import URL

from .cmd_utils import run_cmd_line
from .exceptions import CmdLineError, ConfigurationError
from .subtask import SubTask

log = logging.getLogger(__name__)

NUMBER_OF_ATTEMPS = 5
MAX_TIME_TO_WAIT_S = 10

RepoID = str
StatusStr = str


@dataclass(frozen=True)
class WatchedGitRepoConfig:
    """Config of the to be observed

    Corresponds to an item of the config's 'watched_git_repositories' list

    Example:
    - id: simcore-github-repo
      url: https://github.com/ITISFoundation/osparc-simcore.git
      branch: master
      username: foo
      password: secret
      tags: ^testtag_v[0-9]+.[0-9]+.[0-9]+$
      paths:
        - services/docker-compose.yml
        - .env-devel
        - .env-wb-garbage-collector
    """

    repo_id: RepoID
    repo_url: URL
    branch: str
    tags: str  # regex or blank
    username: str
    password: str
    paths: list[Path]  # lists the files where to look for changes in the repo


class GitRepo(WatchedGitRepoConfig):
    directory: str = ""


@dataclass(frozen=True)
class RepoStatus:
    """git status of current repo's checkout"""

    repo_id: RepoID
    commit_sha: str
    branch_name: str
    # tag info (only one)
    tag_name: Optional[str] = None
    tag_created: Optional[datetime] = None

    def to_string(self) -> StatusStr:
        return (
            f"{self.repo_id}:{self.branch_name}:{self.tag_name}:{self.commit_sha}"
            if self.tag_name
            else f"{self.repo_id}:{self.branch_name}:{self.commit_sha}"
        )

    def __post_init__(self):
        # tag_created default if undefined
        if self.tag_name and self.tag_created is None:
            # SEE https://stackoverflow.com/questions/53756788/how-to-set-the-value-of-dataclass-field-in-post-init-when-frozen-true
            assert hasattr(self, "tag_created")  # nosec
            object.__setattr__(self, "tag_created", datetime.now(tz=timezone.utc))


#
# git CLI utils
#


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
    return sha


async def _git_get_sha_of_tag(directory: str, tag: str) -> str:
    cmd = ["git", "rev-list", "-1", "--sparse", tag]
    sha_long = await run_cmd_line(cmd, f"{directory}")
    cmd = ["git", "rev-parse", "--short", sha_long]
    sha_short = await run_cmd_line(cmd, f"{directory}")
    return sha_short


async def _git_get_tag_created_dt(directory: str, tag: str) -> Optional[datetime]:
    """
    Returns tagger timestamp if exists, otherwise None

    raises ValueError if invalid datetime format
    """
    date_string = await run_cmd_line(
        ["git", "for-each-ref", "--format='%(taggerdate)'", f"refs/tags/{tag}"],
        f"{directory}",
    )

    # NOTE:
    #  $ tag -a test -m "Tagging <tag-name>"
    #  $ git for-each-ref --format='%(taggerdate)' refs/tags/test
    #    Tue Feb 28 20:34:50 2023 +0100
    # Nonetheless, tags produced by **Github release workflow DO NOT HAVE taggerdate**
    #
    if date_string := date_string.strip("'\n "):
        # e.g. Tue Feb 28 20:34:50 2023 +0100
        date_format = "%a %b %d %H:%M:%S %Y %z"
        return datetime.strptime(date_string, date_format)
    return None


async def _git_clean_repo(directory: str):
    cmd = ["git", "clean", "-dxf"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_checkout_files(directory: str, paths: list[Path], tag: Optional[str]):
    if not tag:
        tag = "HEAD"
    cmd = ["git", "checkout", tag] + [f"{path}" for path in paths]
    await run_cmd_line(cmd, f"{directory}")


async def _git_pull(directory: str):
    cmd = ["git", "pull"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_fetch(directory: str):
    log.debug("Fetching git repo in %s", f"{directory=}")
    cmd = ["git", "fetch", "--prune", "--tags"]
    await run_cmd_line(cmd, f"{directory}")


async def _git_get_latest_matching_tag_capture_groups(
    directory: str, regexp: str
) -> Optional[tuple[str]]:  # pylint: disable=unsubscriptable-object
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
    regexp_compiled = re.compile(regexp)
    list_tags = re.findall(regexp, "  ".join(all_tags))
    if not list_tags:
        return None
    if regexp_compiled.groups == 0:
        return (list_tags[-1],)
    re_search_result = re.search(regexp, list_tags[-1])
    return re_search_result.groups() if re_search_result else None


async def _git_get_latest_matching_tag(
    directory: str, regexp: str
) -> Optional[str]:  # pylint: disable=unsubscriptable-object
    repo_tags_msg = await run_cmd_line(
        [
            "git",
            "tag",
            "--list",
            "--sort=creatordate",  # Sorted ascending by date
        ],
        f"{directory}",
    )
    if repo_tags_msg is None:
        return None
    all_tags = [tag for tag in repo_tags_msg.split("\n") if tag != ""]
    list_tags = [tag for tag in all_tags if re.search(regexp, tag) != None]
    return list_tags[-1] if list_tags else None


async def _git_get_current_matching_tag(repo: GitRepo) -> list[str]:
    # NOTE: there might be several tags on the same commit
    reg = repo.tags
    if repo.tags.startswith("^"):
        reg = repo.tags[1:]

    all_tags_str = await run_cmd_line(
        [
            "git",
            "show-ref",
            "--tags",
            "--dereference",
        ],
        f"{repo.directory}",
    )

    if all_tags_str is None:
        return []

    all_tags = all_tags_str.split("\n")

    cmd2 = ["git", "rev-parse", "HEAD"]
    sha_to_be_found = await run_cmd_line(cmd2, f"{repo.directory}")
    sha_to_be_found = sha_to_be_found.split("\n")[0]

    associated_tags_found = []
    for tag in all_tags:
        if sha_to_be_found in tag:
            associated_tags_found.append(tag.split()[-1].split("refs/tags/")[-1])
    found_matching_tags = []
    for i in associated_tags_found:
        if re.search(reg, i):
            found_matching_tags += [i]
    return found_matching_tags


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
    logs = await run_cmd_line(cmd, f"{directory}", strip_endline=False)
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


#
# repository utils
#


async def _checkout_repository(repo: GitRepo, tag: Optional[str] = None):
    """
    :raises ConfigurationError
    """
    await _git_checkout_files(repo.directory, [], tag)
    cmd = ["find", "."]
    files_in_repo = (await run_cmd_line(cmd, f"{repo.directory}")).split("\n")
    are_all_files_present = sum(
        1 for i in repo.paths if i in [i.replace("./", "") for i in files_in_repo]
    ) == len(repo.paths)
    if not are_all_files_present:
        # no change affected the watched files
        raise ConfigurationError("no change affected the watched files")


async def _pull_repository(repo: GitRepo):
    await _git_pull(repo.directory)


async def _clone_and_checkout_repositories(
    repos: list[GitRepo], aio_stack: AsyncExitStack
) -> dict[RepoID, RepoStatus]:
    repo_2_status = {}
    for repo in repos:
        tmpdir: str = await aio_stack.enter_async_context(
            TemporaryDirectory(prefix=f"{repo.repo_id}_")
        )
        repo.directory = tmpdir
        log.debug("cloning %s to %s...", repo.repo_id, repo.directory)

        await _git_clone_repo(
            repository=repo.repo_url,
            directory=repo.directory,
            branch=repo.branch,
            username=repo.username,
            password=repo.password,
        )
        await _git_fetch(repo.directory)

        latest_tag: Optional[str] = (
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
        await _git_checkout_files(repo.directory, [], latest_tag)
        # This subsequent call will checkout the files at the given revision
        await _checkout_repository(repo, latest_tag)

        log.info("repository %s checked out on %s", repo, latest_tag)

        # If no tag: fetch head
        # if tag: sha of tag
        created = None
        if repo.tags and latest_tag:
            sha = await _git_get_sha_of_tag(repo.directory, latest_tag)
            created = await _git_get_tag_created_dt(repo.directory, latest_tag)
        else:
            sha = await _git_get_FETCH_HEAD_sha(repo.directory)

        log.debug("sha for %s is %s at %s", repo.repo_id, sha, created)

        repo_2_status[repo.repo_id] = RepoStatus(
            repo_id=repo.repo_id,
            branch_name=repo.branch,
            commit_sha=sha,
            tag_name=latest_tag,
            tag_created=created,
        )
    return repo_2_status


async def _check_if_tag_on_branch(repo_path: str, branch: str, tag: str) -> bool:
    cmd = [
        "git",
        "log",
        "--tags",
        "--simplify-by-decoration",
        '--pretty="format:%ai %d"',
    ]
    try:
        data = await run_cmd_line(cmd, repo_path)
    except CmdLineError as e:
        raise RuntimeError(
            " ".join(cmd), "The command was invalid and the cmd call failed."
        ) from e
    if not data:
        return False
    for line in data.split("\n"):
        if branch in line and tag in line:
            return True
    found_branch_in_data = sum(1 for i in data.split("\n") if branch in i) > 0
    found_tag_in_data = sum(1 for i in data.split("\n") if tag in i) > 0

    if not found_branch_in_data:
        raise RuntimeError("Branch does not exist. Aborting!")
    if not found_tag_in_data:
        raise RuntimeError("Tag does not exist. Aborting!")
    return False


async def _update_repo_using_tags(repo: GitRepo) -> Optional[RepoStatus]:
    """

    returns RepoStatus if changes in repo detected otherwise None

    :raises ConfigurationError
    """

    log.debug("checking %s using tags", repo.repo_id)
    # check if current tag is the latest and greatest
    list_current_tags = await _git_get_current_matching_tag(repo)
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
    else:
        log.info("New tag detected: %s on repo %s", latest_tag, repo.repo_id)

    # get modifications
    logged_changes = await _git_get_logs_tags(
        repo.directory, list_current_tags[0], latest_tag
    )
    log.debug("%s tag changes: %s", latest_tag, logged_changes)

    # checkout no matter if there are changes, to put HEAD of git repo at desired latest matching tag
    await _checkout_repository(repo, latest_tag)

    # Report if code changed only
    if latest_tag not in list_current_tags:
        log.info("New tag %s checked out on repo %s", latest_tag, repo.repo_id)

        # if the tag changed, an update is needed even if no files were changed
        sha = await _git_get_sha_of_tag(repo.directory, latest_tag)

        return RepoStatus(
            repo_id=repo.repo_id,
            commit_sha=sha,
            branch_name=repo.branch,
            tag_name=latest_tag,
        )

    return None


async def _update_repo_using_branch_head(repo: GitRepo) -> Optional[RepoStatus]:
    """
    returns RepoStatus if changes in repo detected otherwise None
    """
    modified_files_str = await _git_diff_filenames(repo.directory)
    if not modified_files_str:
        # no modifications
        return None
    modified_files = modified_files_str.split()

    # get the logs
    logged_changes = await _git_get_logs(repo.directory, repo.branch, repo.branch)
    log.debug("Changelog:\n%s", logged_changes)
    await _pull_repository(repo)

    # check if a watched file has changed
    common_files = (
        set(modified_files).intersection(set(repo.paths))
        if repo.paths
        else modified_files
    )
    if not common_files:
        # no change affected the watched files
        return None

    log.info("File %s changed!!", common_files)
    sha = await _git_get_FETCH_HEAD_sha(repo.directory)
    return RepoStatus(
        repo_id=repo.repo_id, commit_sha=sha, branch_name=repo.branch, tag_name=None
    )


async def _check_for_changes_in_repositories(
    repos: list[GitRepo],
    syncedViaTags: bool = False,
) -> dict[RepoID, RepoStatus]:
    """
    raises ConfigurationError
    """
    changes: dict[RepoID, RepoStatus] = {}
    for repo in repos:
        log.debug("fetching repo: %s...", repo.repo_url)
        await _git_fetch(repo.directory)
    latest_tags = [
        {
            repo.repo_id: await _git_get_latest_matching_tag_capture_groups(
                repo.directory, repo.tags
            )
        }
        for repo in repos
    ]
    uniqueLatestTags = list(
        {
            list(single_tag.values())[0][0] if list(single_tag.values())[0] else None
            for single_tag in latest_tags
            if single_tag.values()
        }
    )
    if syncedViaTags:
        if len(uniqueLatestTags) > 1:
            log.info("Repos did not match in their latest tag's first capture group!")
            log.info(
                "Latest (matching) tags per repo, displaying first regex capture group:"
            )
            for repo in latest_tags:
                log.info("%s: %s", list(repo.keys())[0], list(repo.values())[0][0])
            log.info("Will only update those repos that have no tag-regex specified!")
        elif len(uniqueLatestTags) == 1:
            log.info("All synced repos have the same latest tag! Deploying....")
    for repo in repos:
        if syncedViaTags and len(uniqueLatestTags) > 1 and repo.tags:
            continue
        log.debug("checking repo: %s...", repo.repo_url)
        await _git_clean_repo(repo.directory)

        if repo.tags:
            latest_matching_tag = await _git_get_latest_matching_tag(
                repo.directory, repo.tags
            )
            if latest_matching_tag is None:
                raise ConfigurationError(
                    msg=f"no tags found in {repo.repo_id} that follows defined tags pattern {repo.tags}"
                )

            if not await _check_if_tag_on_branch(
                repo.directory,
                repo.branch,
                latest_matching_tag,
            ):
                continue
        # changes in repo
        repo_changes: Optional[RepoStatus] = (
            await _update_repo_using_tags(repo)
            if repo.tags
            else await _update_repo_using_branch_head(repo)
        )
        if repo_changes:
            changes[repo.repo_id] = repo_changes

    return changes


async def _delete_repositories(repos: list[GitRepo]) -> None:
    for repo in repos:
        await remove_directory(Path(repo.directory), ignore_errors=True)


#
# SubTask interface
#


class GitUrlWatcher(SubTask):
    def __init__(self, app_config: dict[str, Any]):
        super().__init__(name="git repo watcher")
        self.synced_via_tags = app_config["main"]["synced_via_tags"]
        self.watched_repos: list[GitRepo] = [
            GitRepo(
                repo_id=config["id"],
                repo_url=config["url"],
                branch=config["branch"],
                tags=config["tags"],
                username=config["username"],
                password=config["password"],
                paths=config["paths"],
            )
            for config in app_config["main"]["watched_git_repositories"]
        ]

        self.repo_status: dict[RepoID, RepoStatus] = {}
        self._aiostack = AsyncExitStack()

    async def init(self) -> dict[RepoID, StatusStr]:
        # SubTask Override
        log.info("initializing git repositories...")
        self.repo_status = await _clone_and_checkout_repositories(
            self.watched_repos, self._aiostack
        )

        return {
            repo_id: status.to_string() for repo_id, status in self.repo_status.items()
        }

    @retry(
        reraise=True,
        stop=stop_after_attempt(NUMBER_OF_ATTEMPS),
        wait=wait_random(min=1, max=MAX_TIME_TO_WAIT_S),
        after=after_log(log, logging.DEBUG),
    )
    async def check_for_changes(self) -> dict[RepoID, StatusStr]:
        # SubTask Override
        repos_changes = await _check_for_changes_in_repositories(
            repos=self.watched_repos
        )
        changes = {
            repo_id: repo_status.to_string()
            for repo_id, repo_status in repos_changes.items()
        }
        return changes

    async def cleanup(self):
        # SubTask Override
        await self._aiostack.aclose()
        await _delete_repositories(repos=self.watched_repos)


__all__: tuple[str, ...] = ("GitUrlWatcher",)
