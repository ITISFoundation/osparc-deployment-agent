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

from .exceptions import CmdLineError, ConfigurationError, TagSyncErrorException
from .subprocess_utils import exec_command_async
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
    await exec_command_async(cmd)


async def _git_get_FETCH_HEAD_sha(directory: str) -> str:
    cmd = ["git", "rev-parse", "--short", "FETCH_HEAD"]
    sha = await exec_command_async(cmd, f"{directory}")
    return sha


async def _git_get_sha_of_tag(directory: str, tag: str) -> str:
    cmd = ["git", "rev-list", "-1", "--sparse", tag]
    sha_long = await exec_command_async(cmd, f"{directory}")
    cmd = ["git", "rev-parse", "--short", sha_long]
    sha_short = await exec_command_async(cmd, f"{directory}")
    return sha_short


async def _git_get_tag_created_dt(directory: str, tag: str) -> Optional[datetime]:
    """
    Returns tagger timestamp if exists, otherwise None

    raises ValueError if invalid datetime format
    """
    date_string = await exec_command_async(
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
    if (
        not date_string
    ):  # Likely not an annoted tag, no taggerdate. We check the commit date.
        # via https://stackoverflow.com/users/2496266/chaim-eliyah
        date_string = await exec_command_async(
            ["git", "log", "-1", "--format=%aI,", tag],
            f"{directory}",
        )
        if date_string := date_string.strip("'\n ").strip(","):
            return datetime.fromisoformat(date_string)
    return None


async def _git_clean_repo(directory: str):
    cmd = ["git", "clean", "-dxf"]
    await exec_command_async(cmd, f"{directory}")


async def _git_checkout_files(directory: str, paths: list[Path], tag: Optional[str]):
    if not tag:
        tag = "HEAD"
    cmd: list[str] = ["git", "checkout", tag] + [f"{path}" for path in paths]
    await exec_command_async(cmd, f"{directory}")


async def _git_pull(directory: str):
    cmd: list[str] = ["git", "pull"]
    await exec_command_async(cmd, f"{directory}")


async def _git_fetch(directory: str) -> Optional[str]:
    log.debug("Fetching git repo in %s", f"{directory=}")
    cmd: list[str] = ["git", "fetch", "--prune", "--tags", "--prune-tags", "--force"]
    # via https://stackoverflow.com/questions/1841341/remove-local-git-tags-that-are-no-longer-on-the-remote-repository/16311126#comment91809130_16311126
    return await exec_command_async(cmd, f"{directory}")


async def _git_get_latest_matching_tag_capture_groups(
    directory: str, regexp: str
) -> Optional[tuple[str]]:
    cmd = [
        "git",
        "tag",
        "--list",
        "--sort=creatordate",  # Sorted ascending by date
    ]
    all_tags = await exec_command_async(cmd, f"{directory}")
    if all_tags == None:
        return None
    all_tags = all_tags.split("\n")
    all_tags = [tag for tag in all_tags if tag != ""]
    regexp_compiled = re.compile(regexp)
    list_tags = [tag for tag in all_tags if re.search(regexp, tag) != None]
    if not list_tags:
        return None
    if regexp_compiled.groups == 0:
        return (list_tags[-1],)
    re_search_result = re.search(regexp, list_tags[-1])
    return re_search_result.groups() if re_search_result else None


async def _git_get_matching_tags_on_branch(
    directory: str, regexp: str, branch: Optional[str] = None
) -> Optional[list[str]]:  # pylint: disable=unsubscriptable-object
    repo_tags_msg = await exec_command_async(
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
    regex_matching_tags = [tag for tag in all_tags if re.search(regexp, tag) != None]
    if branch is not None:
        regex_matching_tags_on_branch = [
            tag
            for tag in regex_matching_tags
            if await _check_if_tag_on_branch(directory, tag, branch)
        ]
        return regex_matching_tags_on_branch
    return regex_matching_tags


async def _git_get_latest_matching_tag_on_branch(
    directory: str, regexp: str, branch: Optional[str] = None
) -> Optional[str]:
    list_tags = await _git_get_matching_tags_on_branch(directory, regexp, branch)
    return list_tags[-1] if list_tags else None


async def _git_get_current_matching_tag(repo: GitRepo) -> list[str]:
    # NOTE: there might be several tags on the same commit
    reg = repo.tags
    if repo.tags.startswith("^"):
        reg = repo.tags[1:]

    all_tags_str = await exec_command_async(
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
    sha_to_be_found = await exec_command_async(cmd2, f"{repo.directory}")
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
    modified_files = await exec_command_async(cmd, f"{directory}")
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
    logs = await exec_command_async(cmd, f"{directory}", strip_endline=False)
    return logs


async def _git_get_logs_tags(
    directory: str, tag1: Optional[str], tag2: str
) -> Optional[str]:
    cmd = [
        "git",
        "--no-pager",
        "log",
        "--oneline",
        f"{tag1 if tag1 else tag2}..{tag2}",
    ]
    logs = await exec_command_async(cmd, f"{directory}")
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
    files_in_repo = (await exec_command_async(cmd, f"{repo.directory}")).split("\n")
    are_all_files_present = sum(
        1 for i in repo.paths if i in [i.replace("./", "") for i in files_in_repo]
    ) == len(repo.paths)
    if not are_all_files_present:
        # no change affected the watched files
        raise ConfigurationError("no change affected the watched files")


async def _pull_repository(repo: GitRepo):
    await _git_pull(repo.directory)


async def _clone_and_checkout_repositories(
    repos: list[GitRepo], aio_stack: AsyncExitStack, synced_via_tags: bool
) -> dict[RepoID, RepoStatus]:
    repo_2_status = {}
    # Initializing repos
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

    # Checking tags

    if synced_via_tags:
        # Sanity check
        at_least_one_repo_has_tag_regex = any(repo.tags for repo in repos)
        if not at_least_one_repo_has_tag_regex:
            raise ConfigurationError(
                "At least one repo must have a tag-regex specified with tag-sync!"
            )
        #
        tags_to_checkout = await _find_matching_tag_to_checkout_tag_sync(repos)

        if not tags_to_checkout:
            log.info("Repos did not match in their git tags.")
            log.info(
                "Hint: We determined the following latest (matching) tags per repo:"
            )
            for repo_tag_info in await _get_repos_latest_tags(repos):
                log.info("%s: %s", repo_tag_info[0], repo_tag_info[1])
            raise TagSyncErrorException(
                "Repos did not match in their latest tag (or, if a tag-regex contains capture groups, their first capture group), but synced_via_tags is activated!"
            )
    else:
        tags_to_checkout = {repo.repo_id: {"repo_id": repo.repo_id} for repo in repos}
    for repo in repos:
        if not "tag_to_checkout" in tags_to_checkout[repo.repo_id].keys():
            if repo.tags:
                latest_tag: Optional[str] = (
                    await _git_get_latest_matching_tag_on_branch(
                        repo.directory, repo.tags, repo.branch
                    )
                    if repo.tags
                    else None
                )
                if not latest_tag:
                    raise ConfigurationError(
                        msg=f"no tags found in {repo.repo_url}:{repo.branch} that matches the given tags regex pattern {repo.tags}."
                    )
                tags_to_checkout[repo.repo_id]["tag_to_checkout"] = latest_tag
            else:
                tags_to_checkout[repo.repo_id]["tag_to_checkout"] = "HEAD"
            log.debug(
                "tag found for %s is %s, now checking out...",
                repo.repo_id,
                tags_to_checkout[repo.repo_id]["tag_to_checkout"],
            )

        # This subsequent call will checkout the files at the given revision
        await _checkout_repository(
            repo, tags_to_checkout[repo.repo_id]["tag_to_checkout"]
        )

        log.info(
            "repository %s checked out on %s",
            repo,
            tags_to_checkout[repo.repo_id]["tag_to_checkout"],
        )

        # If no tag: fetch head
        # if tag: sha of tag
        created = None
        if repo.tags:
            sha = await _git_get_sha_of_tag(
                repo.directory, tags_to_checkout[repo.repo_id]["tag_to_checkout"]
            )
            created = await _git_get_tag_created_dt(
                repo.directory, tags_to_checkout[repo.repo_id]["tag_to_checkout"]
            )
        else:
            sha = await _git_get_FETCH_HEAD_sha(repo.directory)

        log.debug("sha for %s is %s at %s", repo.repo_id, sha, created)

        repo_2_status[repo.repo_id] = RepoStatus(
            repo_id=repo.repo_id,
            branch_name=repo.branch,
            commit_sha=sha,
            tag_name=tags_to_checkout[repo.repo_id]["tag_to_checkout"],
            tag_created=created,
        )
    return repo_2_status


async def _check_if_tag_on_branch(repo_path: str, branch: str, tag: str) -> bool:
    # assert the branch exists:
    cmd = ["git", "rev-parse", "--verify", branch]
    try:
        data = await exec_command_async(cmd, repo_path)
    except CmdLineError as e:
        raise RuntimeError("Branch", branch, " does not exist. Aborting!") from e
    cmd = [
        "git",
        "log",
        "--tags",
        "--simplify-by-decoration",
        '--pretty="format:%ai %d"',
    ]
    try:
        data = await exec_command_async(cmd, repo_path)
    except CmdLineError as e:
        raise RuntimeError(
            " ".join(cmd), "The command was invalid and the cmd call failed."
        ) from e
    if not data:
        return False
    for line in data.split("\n"):
        if branch in line and tag in line:
            return True
    found_tag_in_data = sum(1 for i in data.split("\n") if tag in i) > 0

    if not found_tag_in_data:
        raise RuntimeError("Tag", tag, " does not exist. Aborting!")
    return False


async def _update_repo_using_tags(
    repo: GitRepo, tag_to_checkout: str
) -> Optional[RepoStatus]:
    """

    returns RepoStatus if changes in repo detected otherwise None

    :raises ConfigurationError
    """

    log.debug("checking %s using tags", repo.repo_id)
    # check if current tag is the latest and greatest
    list_current_tags = await _git_get_current_matching_tag(repo)

    log.debug(
        "following tags found for %s, current: %s, desired: %s",
        repo.repo_id,
        list_current_tags,
        tag_to_checkout,
    )
    if tag_to_checkout in list_current_tags:
        log.debug("No change detected")
    else:
        log.info(
            "New desired tag detected: %s on repo %s", tag_to_checkout, repo.repo_id
        )

    # get modifications
    logged_changes = await _git_get_logs_tags(
        repo.directory,
        list_current_tags[0]
        if len(list_current_tags) > 0
        else await _git_get_FETCH_HEAD_sha(repo.directory),
        tag_to_checkout,
    )
    log.debug("%s tag changes: %s", tag_to_checkout, logged_changes)

    # checkout no matter if there are changes, to put HEAD of git repo at desired matching tag
    await _checkout_repository(repo, tag_to_checkout)

    # Report if code changed only
    if tag_to_checkout not in list_current_tags:
        # if the tag changed, an update is needed even if no files were changed
        sha = await _git_get_sha_of_tag(repo.directory, tag_to_checkout)

        return RepoStatus(
            repo_id=repo.repo_id,
            commit_sha=sha,
            branch_name=repo.branch,
            tag_name=tag_to_checkout,
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


async def _git_sha_of_tag(repo_path: str, tag: str) -> str:
    cmd = ["git", "rev-list", "-n", "1", tag]
    try:
        data = await exec_command_async(cmd, repo_path)
    except CmdLineError as e:
        raise RuntimeError(
            " ".join(cmd), "The command was invalid and the cmd call failed."
        ) from e
    if not data:
        raise RuntimeError(
            "Tag", tag, " does not exist on repo", repo_path, ". Aborting!"
        )
    return data.strip()


async def _get_tags_associated_to_sha(repo_path: str, sha: str) -> list[str]:
    cmd = ["git", "tag", "--points-at", sha]
    try:
        data = await exec_command_async(cmd, repo_path)
    except CmdLineError as e:
        raise RuntimeError(
            " ".join(cmd), "The command was invalid and the cmd call failed."
        ) from e
    if not data:
        return []
    return data.split()


async def _get_repos_latest_tags(
    repos: list[GitRepo],
) -> list[Any]:
    each_repo_latest_tags = []
    for repo in repos:
        if not repo.tags:
            continue
        current_regexp = repo.tags
        any_matching_tag = (
            await _git_get_latest_matching_tag_on_branch(  # This returns only one tag
                repo.directory, repo.tags
            )
        )
        if not any_matching_tag:
            continue
        sha_of_tag = await _git_sha_of_tag(repo.directory, any_matching_tag)
        all_tags_of_sha = await _get_tags_associated_to_sha(repo.directory, sha_of_tag)
        # Retain only regexp-matching tags
        all_matching_tags_of_sha = [
            tag for tag in all_tags_of_sha if re.search(current_regexp, tag) != None
        ]
        each_repo_latest_tags.append((repo.repo_id, all_matching_tags_of_sha))
    return each_repo_latest_tags


async def _find_matching_tag_to_checkout_tag_sync(
    repos: list[GitRepo],
) -> Optional[str]:
    each_repo_matching_tags: list[
        dict[Any]
    ] = []  # A list of tuples of (repo_id, list_of_all_tags_of_latest_tagged_commit)
    for repo in repos:
        if not repo.tags:
            continue
        current_regexp = repo.tags
        current_regexp_compiled = re.compile(current_regexp)
        all_matching_tags = (
            await _git_get_matching_tags_on_branch(  # This returns only one tag
                repo.directory, repo.tags, repo.branch
            )
        )
        if not all_matching_tags:
            continue
        all_matching_tags_first_capture_group = [None for i in all_matching_tags]
        if current_regexp_compiled.groups > 0:
            all_matching_tags_first_capture_group = [
                re.search(current_regexp, tag).groups()[0]
                if len(re.search(current_regexp, tag).groups()) > 0
                else None
                for tag in all_matching_tags
            ]
        each_repo_matching_tags.append(
            {
                "repo_id": repo.repo_id,
                "all_matching_tags": all_matching_tags,
                "all_matching_tags_first_capture_group": all_matching_tags_first_capture_group,
            }
        )
    if not each_repo_matching_tags:
        return None
    #
    matchers = []
    for repo in each_repo_matching_tags:
        matchers.append(
            [
                repo["all_matching_tags"][i]
                if not repo["all_matching_tags_first_capture_group"][i]
                else repo["all_matching_tags_first_capture_group"][i]
                for i in range(len(repo["all_matching_tags"]))
            ]
        )
    common_matchers = set.intersection(*map(set, matchers))

    each_repo_synced_tags = []
    for repo in each_repo_matching_tags:
        all_matching_tags_pruned = []
        all_matching_tags_first_capture_group_pruned = []
        for i in range(len(repo["all_matching_tags"])):
            tag_to_check = (
                repo["all_matching_tags"][i]
                if not repo["all_matching_tags_first_capture_group"][i]
                else repo["all_matching_tags_first_capture_group"][i]
            )
            if not tag_to_check in common_matchers:
                continue
            all_matching_tags_pruned.append(repo["all_matching_tags"][i])
            all_matching_tags_first_capture_group_pruned.append(
                repo["all_matching_tags_first_capture_group"][i]
            )
        if (
            not all_matching_tags_pruned
            or not all_matching_tags_first_capture_group_pruned
        ):
            return None
        each_repo_synced_tags.append(
            {
                "repo_id": repo["repo_id"],
                "all_matching_tags": all_matching_tags_pruned,
                "all_matching_tags_first_capture_group": all_matching_tags_first_capture_group_pruned,
            }
        )
    if not each_repo_synced_tags:
        return None

    # Sort all commit's of remaining tags in all repos by commit date
    # Across all repos, check latest correctly tagged commit first, descend list
    timestamped_list_of_all_matching_commits = []
    for repo in each_repo_synced_tags:
        for i in range(len(repo["all_matching_tags"])):
            tag = repo["all_matching_tags"][i]
            git_url_watcher_repo = [
                repos for repos in repos if repos.repo_id == repo["repo_id"]
            ][0]
            timestamp_of_tagged_commit = await _git_get_tag_created_dt(
                git_url_watcher_repo.directory, tag
            )
            sha_of_tag = await _git_sha_of_tag(git_url_watcher_repo.directory, tag)
            current_info = {
                "repo_id": repo["repo_id"],
                "tag": tag,
                "tag_first_capture_group": repo[
                    "all_matching_tags_first_capture_group"
                ][i],
                "sha": sha_of_tag,
                "timestamp": timestamp_of_tagged_commit,
            }
            timestamped_list_of_all_matching_commits.append(current_info)
    time_sorted_list_of_all_matching_commits = sorted(
        timestamped_list_of_all_matching_commits,
        key=lambda k: k["timestamp"],
        reverse=False,
    )
    latest_commit = time_sorted_list_of_all_matching_commits[-1]
    tag_to_checkout = latest_commit["tag"]
    if latest_commit["tag_first_capture_group"]:
        tag_to_checkout = latest_commit["tag_first_capture_group"]
    repos_tags_to_checkout = {repo["repo_id"]: repo for repo in each_repo_synced_tags}
    for repo in repos_tags_to_checkout.values():
        if any(repo["all_matching_tags_first_capture_group"]):
            for j in range(len(repo["all_matching_tags_first_capture_group"])):
                if repo["all_matching_tags_first_capture_group"][j] == tag_to_checkout:
                    repo["all_matching_tags"] = repo["all_matching_tags"][j]
                    repo["tag_to_checkout"] = repo["all_matching_tags"]
                    repo["all_matching_tags_first_capture_group"] = None
                    break
        else:
            repo["all_matching_tags"] = tag_to_checkout
            repo["tag_to_checkout"] = tag_to_checkout
            repo["all_matching_tags_first_capture_group"] = tag_to_checkout
    return repos_tags_to_checkout


async def _check_for_changes_in_repositories(  # pylint: disable=too-many-branches
    repos: list[GitRepo],
    synced_via_tags: bool = False,
) -> dict[RepoID, RepoStatus]:
    """
    raises ConfigurationError
    """
    changes: dict[RepoID, RepoStatus] = {}
    for repo in repos:
        log.debug("fetching repo: %s...", repo.repo_url)
        await _git_fetch(repo.directory)

    tags_to_checkout = None
    if synced_via_tags:
        tags_to_checkout = await _find_matching_tag_to_checkout_tag_sync(repos)
        if not tags_to_checkout:
            log.info("Repos did not match in their git tags.")
            log.info(
                "Hint: We determined the following latest (matching) tags per repo:"
            )
            for repo_tag_info in await _get_repos_latest_tags(repos):
                log.info("%s: %s", repo_tag_info[0], repo_tag_info[1])
            raise TagSyncErrorException(
                "Repos did not match in their latest tag (or, if a tag-regex contains capture groups, their first capture group), but synced_via_tags is activated!"
            )
    else:
        tags_to_checkout = {repo.repo_id: {"repo_id": repo.repo_id} for repo in repos}
    for repo in repos:
        log.debug("checking for changes in repo: %s...", repo.repo_url)
        await _git_clean_repo(repo.directory)

        if repo.tags:
            if not "tag_to_checkout" in tags_to_checkout[repo.repo_id].keys():
                latest_matching_tag = await _git_get_latest_matching_tag_on_branch(
                    repo.directory, repo.tags, repo.branch
                )
                if latest_matching_tag is None:
                    raise ConfigurationError(
                        msg=f"no tags found in {repo.repo_id} that follows defined tags pattern {repo.tags} on branch {repo.branch}"
                    )
                tags_to_checkout[repo.repo_id]["tag_to_checkout"] = latest_matching_tag
        # changes in repo
        repo_changes: Optional[RepoStatus] = (
            await _update_repo_using_tags(
                repo, tag_to_checkout=tags_to_checkout[repo.repo_id]["tag_to_checkout"]
            )
            if "tag_to_checkout" in tags_to_checkout[repo.repo_id].keys()
            else await _update_repo_using_branch_head(repo)
        )
        if repo_changes:
            changes[repo.repo_id] = repo_changes
            log.info(
                "I have deployed: %s ",
                " , ".join(
                    [
                        str(repo_id)
                        + ":"
                        + str(tags_to_checkout[repo_id]["tag_to_checkout"])
                        for repo_id in tags_to_checkout.keys()
                    ]
                ),
            )

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
                branch=config["branch"] if "branch" in config.keys() else None,
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
            self.watched_repos, self._aiostack, synced_via_tags=self.synced_via_tags
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
            repos=self.watched_repos, synced_via_tags=self.synced_via_tags
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
