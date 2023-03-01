# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable
# pylint: disable=too-many-arguments
# pylint: disable=protected-access


import subprocess
import time
from asyncio import AbstractEventLoop
from pathlib import Path
from typing import Any, Final, Literal

import pytest
from faker import Faker
from servicelib.json_serialization import json_dumps
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed
from yarl import URL

from simcore_service_deployment_agent import git_url_watcher
from simcore_service_deployment_agent.exceptions import ConfigurationError
from simcore_service_deployment_agent.git_url_watcher import GitUrlWatcher
from simcore_service_deployment_agent.models import WebserverExtraEnvirons


@pytest.fixture
def branch_name(faker: Faker) -> str:
    return "pytestMockBranch_" + faker.word()


@pytest.fixture
def tag_name(faker: Faker) -> str:
    return f"staging_SprintName{faker.pyint()}"


def _run_cmd(cmd: str, **kwargs) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        cmd, capture_output=True, check=True, shell=True, encoding="utf-8", **kwargs
    )
    assert result.returncode == 0
    return result.stdout.rstrip() if result.stdout else ""


@pytest.fixture
def git_repository_url(tmp_path: Path, branch_name: str, tag_name: str) -> URL:
    _run_cmd(
        "git init; git config user.name tester; git config user.email tester@test.com",
        cwd=tmp_path,
    )
    _run_cmd(
        f"git checkout -b {branch_name}"
        + "; touch initial_file.txt; git add .; git commit -m 'initial commit';",
        cwd=tmp_path,
    )
    _run_cmd(f'git tag -a {tag_name} -m "Release tag at {branch_name}"', cwd=tmp_path)

    return URL(f"file://localhost{tmp_path}")


@pytest.fixture
def git_config(branch_name: str, git_repository_url: str) -> dict[str, Any]:
    cfg = {
        "main": {
            "watched_git_repositories": [
                {
                    "id": "test-repo-0",
                    "url": f"{git_repository_url}",
                    "branch": branch_name,
                    "tags": "",
                    "paths": [],
                    "username": "",
                    "password": "",
                }
            ],
        }
    }
    return cfg


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

    git_sha: str = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=local_path_var,
    )
    # we should have some changes here now
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    assert change_results == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

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
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    _run_cmd(
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
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    _run_cmd(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=LOCAL_PATH,
    )
    with pytest.raises(RuntimeError):
        await git_url_watcher._check_if_tag_on_branch(
            LOCAL_PATH, "nonexistingBranch", VALID_TAG
        )

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_fails_if_tag_not_found(
    event_loop: AbstractEventLoop, git_config: dict[str, Any]
):
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    _run_cmd(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=LOCAL_PATH,
    )
    with pytest.raises(RuntimeError):
        await git_url_watcher._check_if_tag_on_branch(LOCAL_PATH, BRANCH, "invalid_tag")

    await git_watcher.cleanup()


@pytest.fixture
def git_config_paths(git_config: dict[str, Any]) -> dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    return git_config


async def test_git_url_watcher_paths(
    event_loop: AbstractEventLoop,
    git_config_paths: dict[str, Any],
):
    repo_id_var = git_config_paths["main"]["watched_git_repositories"][0]["id"]
    branch_var = git_config_paths["main"]["watched_git_repositories"][0]["branch"]
    local_path_var = git_config_paths["main"]["watched_git_repositories"][0][
        "url"
    ].replace("file://localhost", "")

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_paths)

    # the file does not exist yet
    with pytest.raises(ConfigurationError):
        init_result = await git_watcher.init()

    # add the file
    _run_cmd(
        "touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv';",
        cwd=local_path_var,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {repo_id_var: f"{repo_id_var}:{branch_var}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    # now modify theonefile.csv
    _run_cmd(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv';",
        cwd=local_path_var,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
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
    with pytest.raises(ConfigurationError):
        init_result = await git_watcher.init()

    # add the file
    VALID_TAG = "teststaging_z1stvalid"
    _run_cmd(
        f"touch theonefile.csv; git add theonefile.csv; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=local_path_var,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    assert init_result == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{VALID_TAG}:{git_sha}"
    }

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; echo 'blahblah' >> my_file.txt; git add my_file.txt; git commit -m 'I added my_file.txt'",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    # now modify theonefile.csv
    # git seems to keep track of commit datetimes only up to seconds, so we need to sleep here to prevent both commits
    # having the same timestamp (FIXME)
    _run_cmd(
        "sleep 2 && echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    INVALID_TAG: Final[str] = "v3.4.5"
    _run_cmd(
        f"git tag {INVALID_TAG}",
        cwd=local_path_var,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    NEW_VALID_TAG: Final[str] = "teststaging_g2ndvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG}",
        cwd=local_path_var,
    )
    #
    change_results: dict = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    # now there should be changes
    assert change_results == {
        repo_id_var: f"{repo_id_var}:{branch_var}:{NEW_VALID_TAG}:{git_sha}"
    }
    #
    #

    NEW_VALID_TAG_ON_SAME_SHA = "teststaging_a3rdvalid"  # type: ignore
    _run_cmd(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=local_path_var,
    )
    # now there should be NO changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha: str = _run_cmd("git rev-parse --short HEAD", cwd=local_path_var)
    assert not change_results

    # Check that tags are sorted in correct order, by tag time, not alphabetically
    assert len(git_watcher.watched_repos) == 1
    NEW_VALID_TAG_ON_SAME_SHA: Literal[
        "teststaging_z4thvalid"
    ] = "teststaging_z4thvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA} && sleep 1;",
        cwd=local_path_var,
    )
    # re: sleep
    # reason: make sure the tag's creator data is proeprly different for NEW_VALID_TAG_ON_SAME_SHA and NEW_VALID_TAG_ON_NEW_SHA, otherwise sorting might fail
    #
    time.sleep(0.6)
    #
    NEW_VALID_TAG_ON_NEW_SHA: Final[
        str
    ] = "teststaging_h5thvalid"  # This name is intentionally "in between" the previous tags when alphabetically sorted
    _run_cmd(
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


async def test_get_release_info_into_environs(
    git_config: dict[str, Any], tag_name: str
):
    # fakes tag filter
    git_config["main"]["watched_git_repositories"][0]["tags"] = f"^{tag_name}$"

    # start task
    git_task = GitUrlWatcher(app_config=git_config)
    status_labels = await git_task.init()
    print(status_labels)

    # find target repo
    target_repos = [
        r
        for r in git_task.watched_repos
        if URL(r.repo_url).path.endswith("osparc-simcore.git")
    ]
    target_repos = git_task.watched_repos
    assert len(target_repos) == 1

    # osparc-simcore.git repo
    repo = target_repos[0]
    repo_status = git_task.repo_status[repo.repo_id]

    assert repo_status.tag_name == tag_name

    # if it raises, we do not inject extra environs, otherwise we do
    extra_environs = WebserverExtraEnvirons.parse_obj(
        {
            "SIMCORE_VCS_RELEASE_TAG": repo_status.tag_name,
            "SIMCORE_VCS_RELEASE_DATE": repo_status.tag_created,
        }
    ).dict()

    print(json_dumps(extra_environs, indent=1))
