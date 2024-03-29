# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable
# pylint: disable=too-many-arguments
# pylint: disable=protected-access

import re
import time
import uuid
from asyncio import AbstractEventLoop
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Literal

import pytest
from faker import Faker
from pydantic import parse_obj_as
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed
from yarl import URL

from simcore_service_deployment_agent import git_url_watcher
from simcore_service_deployment_agent.exceptions import (
    ConfigurationError,
    TagSyncErrorException,
)
from simcore_service_deployment_agent.git_url_watcher import (
    GitUrlWatcher,
    _git_get_tag_created_dt,
)
from simcore_service_deployment_agent.subprocess_utils import (
    exec_command_async,
    run_command,
)

RETRYING_PARAMETERS: dict[str, Any] = {
    "stop": stop_after_attempt(10),
    "wait": wait_fixed(3),
}


def sleep_1_sec_to_make_commit_timestamp_unique():
    # git seems to keep track of commit datetimes only up to seconds, so we need to sleep here to prevent both commits
    # having the same timestamp (FIXME)
    time.sleep(1.1)


@pytest.fixture
def branch_name(faker: Faker) -> str:
    return "pytestMockBranch_" + faker.word()


@pytest.fixture
def tag_name(faker: Faker) -> str:
    return f"staging_SprintName{faker.pyint(min_value=0)}"


@pytest.fixture
def git_repository_url(
    tmp_path: Path, branch_name: str, tag_name: str
) -> Callable[[], URL]:
    def _git_repository_url() -> URL:
        subpath = tmp_path / str(uuid.uuid4())
        subpath.mkdir()
        run_command(
            "git init; git config user.name tester; git config user.email tester@test.com",
            cwd=subpath,
        )
        run_command(
            f"git checkout -b {branch_name}"
            + "; touch initial_file.txt; git add .; git commit -m 'initial commit';",
            cwd=subpath,
        )
        run_command(
            f'git tag -a {tag_name} -m "Release tag at {branch_name}"', cwd=subpath
        )
        return URL(f"file://localhost{subpath}")

    return _git_repository_url


@pytest.fixture
def git_repository_folder(git_repository_url: URL) -> Callable[[], Path]:
    def _git_repository_folder() -> Path:
        current_url = git_repository_url()
        assert f"{current_url}".startswith("file://localhost")
        return Path(current_url.path)

    return _git_repository_folder


@pytest.fixture
def watch_tags() -> str:
    return ""


@pytest.fixture
def watch_paths() -> list[str]:
    return []


@pytest.fixture
def git_config(
    branch_name: str,
    git_repository_url: Callable[[], str],
    watch_tags: str,
    watch_paths: list[str],
) -> dict[str, Any]:
    cfg = {
        "main": {
            "synced_via_tags": False,
            "watched_git_repositories": [
                {
                    "id": "test-repo-0",
                    "url": f"{git_repository_url()}",
                    "branch": branch_name,
                    "tags": watch_tags,
                    "paths": watch_paths,
                    "username": "",
                    "password": "",
                }
            ],
        }
    }
    return cfg


@pytest.fixture()
def git_config_two_repos_synced_same_tag_regex(
    branch_name: str, git_repository_url: Callable[[], str]
) -> dict[str, Any]:
    cfg = {
        "main": {
            "synced_via_tags": True,
            "watched_git_repositories": [
                {
                    "id": "test-repo-" + str(i),
                    "url": f"{git_repository_url()}",
                    "branch": branch_name,
                    "tags": "^staging_.*$",
                    "paths": ["testfile.csv"],
                    "username": "",
                    "password": "",
                }
                for i in range(2)
            ],
        }
    }
    return cfg


@pytest.fixture()
def git_config_two_repos_synced_capture_group_tag_regex(
    branch_name: str, git_repository_url: Callable[[], str]
) -> dict[str, Any]:
    cfg = {
        "main": {
            "synced_via_tags": True,
            "watched_git_repositories": [
                {
                    "id": "test-repo-" + str(0),
                    "url": f"{git_repository_url()}",
                    "branch": branch_name,
                    "tags": "^staging_.*$",
                    "paths": ["testfile.csv"],
                    "username": "",
                    "password": "",
                },
                {
                    "id": "test-repo-" + str(1),
                    "url": f"{git_repository_url()}",
                    "branch": branch_name,
                    "tags": "^test(staging_.*)$",
                    "paths": ["testfile.csv"],
                    "username": "",
                    "password": "",
                },
            ],
        }
    }
    return cfg


async def test_git_url_watcher_tag_sync(
    event_loop, git_config_two_repos_synced_same_tag_regex: dict[str, Any]
):
    branch_var: str = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["branch"]
    local_path_var: str = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["url"].replace("file://localhost", "")

    assert git_config_two_repos_synced_same_tag_regex["main"]["synced_via_tags"]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_same_tag_regex
    )
    #

    # add a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_z1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    sleep_1_sec_to_make_commit_timestamp_unique()
    for repo in [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]:
        run_command(
            f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
        assert await git_url_watcher._check_if_tag_on_branch(
            repo["url"].replace("file://localhost", ""), branch_var, VALID_TAG
        )
    init_result = await git_watcher.init()
    assert not await git_watcher.check_for_changes()
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Add change and tag in only one repo
    VALID_TAG_2: Literal["staging_a2ndvalid"] = "staging_a2ndvalid"
    run_command(
        f"touch {TESTFILE_NAME}_2; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}_2'; git tag {VALID_TAG_2}",
        cwd=local_path_var,
    )
    # we should have no change here, since the repos are synced.
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Now change both repos
    VALID_TAG_3: Literal["staging_g2ndvalid"] = "staging_g2ndvalid"
    for repo in [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]:
        run_command(
            f"touch {TESTFILE_NAME}_3; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG_3};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    assert change_results

    await git_watcher.cleanup()


async def test_git_url_watcher_find_new_file(
    event_loop: AbstractEventLoop, git_config: dict[str, Any]
):
    local_path_var = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    repo_id_var = git_config["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config["main"]["watched_git_repositories"][0]["branch"]

    git_watcher: git_url_watcher.GitUrlWatcher = git_url_watcher.GitUrlWatcher(
        git_config
    )
    init_result = await git_watcher.init()

    git_sha: str = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    run_command(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=local_path_var,
    )
    # we should have some changes here now
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert change_results == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_fails_if_tag_not_found(
    event_loop: AbstractEventLoop, git_config: dict[str, Any]
):
    branch_var = git_config["main"]["watched_git_repositories"][0]["branch"]
    local_path_var = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    # add the a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_z1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    run_command(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    with pytest.raises(RuntimeError):
        await git_url_watcher._check_if_tag_on_branch(
            local_path_var, branch_var, "invalid_tag"
        )

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_succeeds(
    event_loop: AbstractEventLoop, git_config: dict[str, Any]
):
    local_path_var = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    branch_var = git_config["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    await git_watcher.init()
    # add the a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_z1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    run_command(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    assert await git_url_watcher._check_if_tag_on_branch(
        local_path_var, branch_var, VALID_TAG
    )
    check_for_changes_result = await git_watcher.check_for_changes()
    assert check_for_changes_result
    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_raises_if_branch_doesnt_exist(
    event_loop: AbstractEventLoop, git_config: dict[str, Any]
):
    repo_id_var = git_config["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config["main"]["watched_git_repositories"][0]["branch"]
    local_path_var = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    run_command(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    with pytest.raises(RuntimeError):
        await git_url_watcher._check_if_tag_on_branch(
            local_path_var, "nonexistingBranch", VALID_TAG
        )

    await git_watcher.cleanup()


@pytest.fixture
def git_config_paths(git_config: dict[str, Any]) -> dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    return git_config


async def test_git_url_watcher_paths(
    event_loop: AbstractEventLoop,
    git_config_paths: dict[str, Any],
):
    repo_id_var: str = git_config_paths["main"]["watched_git_repositories"][0]["id"]
    branch_var: str = git_config_paths["main"]["watched_git_repositories"][0]["branch"]
    local_path_var: str = git_config_paths["main"]["watched_git_repositories"][0][
        "url"
    ].replace("file://localhost", "")

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_paths)

    # the file does not exist yet
    #

    # add the file
    run_command(
        "touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv';",
        cwd=local_path_var,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    run_command(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    # now modify theonefile.csv
    run_command(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv';",
        cwd=local_path_var,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert change_results == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    await git_watcher.cleanup()


@pytest.fixture
def git_config_tags(git_config: dict[str, Any]) -> dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    git_config["main"]["watched_git_repositories"][0]["tags"] = "^test(staging_.+)$"
    return git_config


async def test_git_url_watcher_tags(
    event_loop: AbstractEventLoop,
    git_config_tags: dict[str, Any],
):
    local_path_var = git_config_tags["main"]["watched_git_repositories"][0][
        "url"
    ].replace("file://localhost", "")
    repo_id_var = git_config_tags["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config_tags["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_tags)

    # the file does not exist yet
    #

    # add the file
    VALID_TAG = "teststaging_z1stvalid"
    run_command(
        f"touch theonefile.csv; git add theonefile.csv; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{VALID_TAG}:{git_sha}"
    }

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    run_command(
        "touch my_file.txt; echo 'blahblah' >> my_file.txt; git add my_file.txt; git commit -m 'I added my_file.txt'",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    # now modify theonefile.csv
    sleep_1_sec_to_make_commit_timestamp_unique()
    run_command(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    INVALID_TAG: Final[str] = "v3.4.5"
    run_command(
        f"git tag {INVALID_TAG}",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    NEW_VALID_TAG: Final[str] = "teststaging_g2ndvalid"
    run_command(
        f"git tag {NEW_VALID_TAG}",
        cwd=local_path_var,
    )
    #
    change_results: dict = await git_watcher.check_for_changes()
    # get new sha
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    # now there should be changes
    assert change_results == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{NEW_VALID_TAG}:{git_sha}"
    }
    #
    #

    NEW_VALID_TAG_ON_SAME_SHA = "teststaging_a3rdvalid"  # type: ignore
    run_command(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=local_path_var,
    )
    # now there should be NO changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha: str = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert not change_results

    # Check that tags are sorted in correct order, by tag time, not alphabetically
    assert len(git_watcher.watched_repos) == 1
    NEW_VALID_TAG_ON_SAME_SHA: Literal[
        "teststaging_z4thvalid"
    ] = "teststaging_z4thvalid"
    run_command(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=local_path_var,
    )
    sleep_1_sec_to_make_commit_timestamp_unique()
    #
    NEW_VALID_TAG_ON_NEW_SHA: Final[
        str
    ] = "teststaging_h5thvalid"  # This name is intentionally "in between" the previous tags when alphabetically sorted
    run_command(
        f"echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'; git tag {NEW_VALID_TAG_ON_NEW_SHA}",
        cwd=local_path_var,
    )
    ##
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(10),
        wait=wait_fixed(1),
    ):
        with attempt:
            # we should have a change here
            change_results = await git_watcher.check_for_changes()
            latestTag = await git_url_watcher._git_get_latest_matching_tag(
                git_watcher.watched_repos[0].directory,
                git_watcher.watched_repos[0].tags,
            )
            assert latestTag == NEW_VALID_TAG_ON_NEW_SHA
    #
    await git_watcher.cleanup()


async def test_git_url_watcher_tags_capture_group_replacement(
    event_loop: AbstractEventLoop,
    git_config_tags: dict[str, Any],
):
    local_path_var = git_config_tags["main"]["watched_git_repositories"][0][
        "url"
    ].replace("file://localhost", "")
    repo_id_var = git_config_tags["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config_tags["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_tags)

    # the file does not exist yet
    #

    # add the file
    VALID_TAG = "teststaging_z1stvalid"
    run_command(
        f"touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    # expected to work now
    init_result = await git_watcher.init()
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{VALID_TAG}:{git_sha}"
    }

    # there are no changes
    assert not await git_watcher.check_for_changes()

    NEW_VALID_TAG_ON_SAME_SHA: Literal[
        "teststaging_z4thvalid"
    ] = "teststaging_z4thvalid"
    sleep_1_sec_to_make_commit_timestamp_unique()
    run_command(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=local_path_var,
    )
    sleep_1_sec_to_make_commit_timestamp_unique()
    NEW_VALID_TAG_ON_NEW_SHA: Literal[
        "teststaging_h5thvalid"
    ] = "teststaging_h5thvalid"  # This name is intentionally "in between" the previous tags when alphabetically sorted
    run_command(
        f"echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'; git tag {NEW_VALID_TAG_ON_NEW_SHA}",
        cwd=local_path_var,
    )
    # we should have a change here

    change_results = await git_watcher.check_for_changes()
    assert change_results
    latestTag = await git_url_watcher._git_get_latest_matching_tag_capture_groups(
        git_watcher.watched_repos[0].directory, git_watcher.watched_repos[0].tags
    )
    assert latestTag[0] == NEW_VALID_TAG_ON_NEW_SHA.replace("test", "")
    #
    await git_watcher.cleanup()


@pytest.mark.parametrize(
    "watch_tags",
    [
        "^staging_SprintName",
    ],
)
async def test_repo_status(git_config: dict[str, Any], tag_name: str, watch_tags: str):
    # fakes tag filter
    assert re.search(watch_tags, tag_name)

    # evaluates status
    git_task = GitUrlWatcher(app_config=git_config)
    status_label: str = await git_task.init()
    print(status_label)

    # repo
    assert len(git_task.watched_repos) == 1
    repo = git_task.watched_repos[0]
    repo_status = git_task.repo_status[repo.repo_id]

    assert repo_status.tag_name is not None
    assert repo_status.tag_name == tag_name
    assert repo_status.tag_created

    # tests _git_get_tag_created_dt
    tag_created = await _git_get_tag_created_dt(repo.directory, repo_status.tag_name)
    assert tag_created
    assert tag_created == repo_status.tag_created


async def test_date_format_to_pydantic():
    # Tests to ensure datetime formats conversions
    #
    # SIMCORE_VCS_RELEASE_TAG
    # SIMCORE_VCS_RELEASE_DATE

    # $ date --utc +"%Y-%m-%dT%H:%M:%SZ"
    #  2023-03-02T16:27:35Z
    timestamp_dt = parse_obj_as(datetime, "2023-03-02T16:27:35Z")

    # execute
    output = await exec_command_async(["date", "--utc", '+"%Y-%m-%dT%H:%M:%SZ"'])
    print(output)
    SIMCORE_VCS_RELEASE_DATE = output.strip('"')

    # Tests it can be parsed by pydantic as a datetime
    release_dt = parse_obj_as(datetime, SIMCORE_VCS_RELEASE_DATE)
    assert isinstance(release_dt, datetime)
    assert release_dt.tzinfo == timezone.utc


async def test_git_url_watcher_rolls_back_if_tag_on_remote_vanishes(
    event_loop: AbstractEventLoop,
    git_config_tags: dict[str, Any],
):
    local_path_var = git_config_tags["main"]["watched_git_repositories"][0][
        "url"
    ].replace("file://localhost", "")
    repo_id_var = git_config_tags["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config_tags["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_tags)

    # the file does not exist yet
    #

    # add the file
    VALID_TAG = "teststaging_z1stvalid"
    run_command(
        f"touch theonefile.csv; git add theonefile.csv; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{VALID_TAG}:{git_sha}"
    }

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now modify theonefile.csv
    sleep_1_sec_to_make_commit_timestamp_unique()
    run_command(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    #
    NEW_VALID_TAG: Final[str] = "teststaging_g2ndvalid"
    run_command(
        f"git tag {NEW_VALID_TAG}",
        cwd=local_path_var,
    )
    #
    change_results: dict = await git_watcher.check_for_changes()
    # get new sha
    git_sha = run_command("git rev-parse --short HEAD", cwd=local_path_var)
    # now there should be changes
    assert change_results == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{NEW_VALID_TAG}:{git_sha}"
    }
    #
    #
    #
    sleep_1_sec_to_make_commit_timestamp_unique()
    run_command(
        f"git tag -d {NEW_VALID_TAG}",
        cwd=local_path_var,
    )
    #
    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            change_results: dict = await git_watcher.check_for_changes()
            assert change_results
    # get new sha
    # assert {{VALID_TAG}} of local and remote are identical
    watched_repo_git_sha = run_command(
        f"git rev-parse --short {VALID_TAG}", cwd=git_watcher.watched_repos[0].directory
    )
    assert watched_repo_git_sha == run_command(
        f"git rev-parse --short {VALID_TAG}", cwd=local_path_var
    )
    # now there should be changes
    assert change_results == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{VALID_TAG}:{watched_repo_git_sha}"
    }
    #
    #

    await git_watcher.cleanup()


async def test_git_url_watcher_rolls_back_if_tag_on_remote_vanishes_tag_sync(
    event_loop: AbstractEventLoop,
    git_config_two_repos_synced_same_tag_regex: dict[str, Any],
):
    branch_var: str = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["branch"]
    local_path_var: str = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["url"].replace("file://localhost", "")

    assert git_config_two_repos_synced_same_tag_regex["main"]["synced_via_tags"]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_same_tag_regex
    )
    #

    # add a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_z1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    sleep_1_sec_to_make_commit_timestamp_unique()
    _helper_list_watched_repos = [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]
    for repo in _helper_list_watched_repos:
        run_command(
            f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
        assert await git_url_watcher._check_if_tag_on_branch(
            repo["url"].replace("file://localhost", ""), branch_var, VALID_TAG
        )
    init_result = await git_watcher.init()
    git_shas_upon_init = [
        run_command(
            f"git rev-parse --short {VALID_TAG}",
            cwd=repo["url"].replace("file://localhost", ""),
        )
        for repo in _helper_list_watched_repos
    ]
    assert len(git_shas_upon_init) == len(_helper_list_watched_repos)
    assert not await git_watcher.check_for_changes()
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Add change and tag in all repos
    NEW_VALID_TAG: Literal["staging_a2ndvalid"] = "staging_a2ndvalid"
    TESTFILE_NAME_2: Literal["testfile2.csv"] = "testfile2.csv"
    for repo in [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]:
        run_command(
            f"touch {TESTFILE_NAME_2}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME_2}'; git tag {NEW_VALID_TAG};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
        assert await git_url_watcher._check_if_tag_on_branch(
            repo["url"].replace("file://localhost", ""), branch_var, NEW_VALID_TAG
        )
    change_results = await git_watcher.check_for_changes()
    assert change_results
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Remove tag from one repo
    run_command(
        f"git tag -d {NEW_VALID_TAG}",
        cwd=local_path_var,
    )
    # There should be no changes / no deployment as tags dont match

    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            change_results: dict = await git_watcher.check_for_changes()
            assert not change_results

    # Remove tag everywhere
    for repo in [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]:
        run_command(
            f"git tag -d {NEW_VALID_TAG} | true;",
            cwd=repo["url"].replace("file://localhost", ""),
        )
    # We should have changes and effectively roll back
    async for attempt in AsyncRetrying(**RETRYING_PARAMETERS):
        with attempt:
            change_results: dict = await git_watcher.check_for_changes()
            assert change_results
    # assert that we checked out the right code
    for i in range(len(git_shas_upon_init)):
        current_sha = git_shas_upon_init[i]
        assert current_sha == run_command(
            "git rev-parse --short HEAD", cwd=git_watcher.watched_repos[i].directory
        )

    await git_watcher.cleanup()


async def test_git_url_watcher_cannot_init_when_no_commit_on_remote(
    event_loop: AbstractEventLoop,
    git_config_tags: dict[str, Any],
):
    git_watcher = git_url_watcher.GitUrlWatcher(git_config_tags)
    # Cannot init if no file present on remote
    with pytest.raises(ConfigurationError):
        init_result = await git_watcher.init()


async def test_git_url_watcher_tag_sync_with_multiple_tags_per_commit(
    event_loop: AbstractEventLoop,
    git_config_two_repos_synced_same_tag_regex: dict[str, Any],
):
    assert git_config_two_repos_synced_same_tag_regex["main"]["synced_via_tags"]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_same_tag_regex
    )

    # add a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_m1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    sleep_1_sec_to_make_commit_timestamp_unique()
    _helper_list_watched_repos = [
        git_config_two_repos_synced_same_tag_regex["main"]["watched_git_repositories"][
            i
        ]
        for i in range(
            len(
                git_config_two_repos_synced_same_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]
    for repo in _helper_list_watched_repos:
        run_command(
            f"touch initfile; git add .; git commit -m 'init'; touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
    init_result = await git_watcher.init()
    git_shas_upon_init = [
        run_command(
            f"git rev-parse --short {VALID_TAG}",
            cwd=repo["url"].replace("file://localhost", ""),
        )
        for repo in _helper_list_watched_repos
    ]
    assert len(git_shas_upon_init) == len(_helper_list_watched_repos)
    assert not await git_watcher.check_for_changes()
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Add more commits / tags
    for repo in _helper_list_watched_repos:
        run_command(
            f" touch {TESTFILE_NAME}_2; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG}_2;",
            cwd=repo["url"].replace("file://localhost", ""),
        )
    # If we were to check for changes here, there would be some. Now we add more tags to the same commits on one repo.
    # Add 2 tags, alphabetical before and after the first one, without change to only one repo
    NEW_VALID_TAG: Literal[
        "staging_a2ndvalid"
    ] = "staging_a2ndvalid"  # alphabetically before already present tag
    repo1 = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]
    run_command(
        f"git tag {NEW_VALID_TAG};",
        cwd=repo1["url"].replace("file://localhost", ""),
    )
    NEW_VALID_TAG_2: Literal[
        "staging_z3rdvalid"
    ] = "staging_z3rdvalid"  # alphabetically after already present tag
    run_command(
        f"git tag {NEW_VALID_TAG_2};",
        cwd=repo1["url"].replace("file://localhost", ""),
    )

    change_results = await git_watcher.check_for_changes()
    assert change_results  # We should see changes here.

    await git_watcher.cleanup()


async def test_git_url_watcher_tag_sync_with_multiple_tags_per_commit_with_capture_group(
    event_loop: AbstractEventLoop,
    git_config_two_repos_synced_capture_group_tag_regex: dict[str, Any],
):
    assert git_config_two_repos_synced_capture_group_tag_regex["main"][
        "synced_via_tags"
    ]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_capture_group_tag_regex
    )

    # add a file, commit, and tag
    VALID_TAG: Literal["staging_z1stvalid"] = "staging_m1stvalid"
    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    sleep_1_sec_to_make_commit_timestamp_unique()
    _helper_list_watched_repos = [
        git_config_two_repos_synced_capture_group_tag_regex["main"][
            "watched_git_repositories"
        ][i]
        for i in range(
            len(
                git_config_two_repos_synced_capture_group_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]
    repo = _helper_list_watched_repos[0]
    run_command(
        f"touch initfile; git add .; git commit -m 'init'; touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    repo = _helper_list_watched_repos[1]
    run_command(
        f"touch initfile; git add .; git commit -m 'init'; touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag test{VALID_TAG};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    init_result = await git_watcher.init()
    assert not await git_watcher.check_for_changes()
    sleep_1_sec_to_make_commit_timestamp_unique()
    # Add more commits / tags
    repo = _helper_list_watched_repos[0]
    run_command(
        f" touch {TESTFILE_NAME}_2; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG}_2;",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    repo = _helper_list_watched_repos[1]
    run_command(
        f" touch {TESTFILE_NAME}_2; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag test{VALID_TAG}_2;",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    # If we were to check for changes here, there would be some. Now we add more tags to the same commits on one repo.
    # Add 2 tags, alphabetical before and after the first one, without change to only one repo
    NEW_VALID_TAG: Literal[
        "staging_a2ndvalid"
    ] = "staging_a2ndvalid"  # alphabetically before already present tag
    repo1 = git_config_two_repos_synced_capture_group_tag_regex["main"][
        "watched_git_repositories"
    ][0]
    run_command(
        f"git tag {NEW_VALID_TAG};",
        cwd=repo1["url"].replace("file://localhost", ""),
    )
    NEW_VALID_TAG_2: Literal[
        "staging_z3rdvalid"
    ] = "staging_z3rdvalid"  # alphabetically after already present tag
    run_command(
        f"git tag {NEW_VALID_TAG_2};",
        cwd=repo1["url"].replace("file://localhost", ""),
    )

    change_results = await git_watcher.check_for_changes()
    assert change_results  # We should see changes here.

    await git_watcher.cleanup()


async def test_git_url_watcher_tag_sync_works_even_if_raise_upon_init(
    event_loop: AbstractEventLoop,
    git_config_two_repos_synced_capture_group_tag_regex: dict[str, Any],
):
    assert git_config_two_repos_synced_capture_group_tag_regex["main"][
        "synced_via_tags"
    ]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_capture_group_tag_regex
    )

    # add a file, commit, and tag

    TESTFILE_NAME: Literal["testfile.csv"] = "testfile.csv"
    sleep_1_sec_to_make_commit_timestamp_unique()
    _helper_list_watched_repos = [
        git_config_two_repos_synced_capture_group_tag_regex["main"][
            "watched_git_repositories"
        ][i]
        for i in range(
            len(
                git_config_two_repos_synced_capture_group_tag_regex["main"][
                    "watched_git_repositories"
                ]
            )
        )
    ]
    repo = _helper_list_watched_repos[0]
    VALID_TAG: str = "staging_m1stvalid"
    run_command(
        f"touch initfile; git add .; git commit -m 'init'; touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    repo = _helper_list_watched_repos[1]
    VALID_TAG_2: str = "staging_a1stvalid"
    run_command(
        f"touch initfile; git add .; git commit -m 'init'; touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag test{VALID_TAG_2};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    with pytest.raises(TagSyncErrorException):  # Will raise
        init_result = await git_watcher.init()
    #
    assert not await git_watcher.check_for_changes()  # no synced tags
    repo = _helper_list_watched_repos[0]
    VALID_TAG_3: str = "staging_z1stvalid"
    run_command(
        f"touch secondfile; git add .; git commit -m 'secondfile'; git tag {VALID_TAG_3};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    repo = _helper_list_watched_repos[1]
    run_command(
        f"touch secondfile; git add .; git commit -m 'secondfile'; git tag test{VALID_TAG_3};",
        cwd=repo["url"].replace("file://localhost", ""),
    )
    # Now we have matching tags, now there are changes.
    change_results = await git_watcher.check_for_changes()
    assert change_results  # We should see changes here.

    await git_watcher.cleanup()
