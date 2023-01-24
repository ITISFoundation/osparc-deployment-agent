import subprocess

# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=bare-except
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict

import pytest
from pytest import TempPathFactory

from simcore_service_deployment_agent import git_url_watcher
from simcore_service_deployment_agent.cmd_utils import CmdLineError
from simcore_service_deployment_agent.exceptions import ConfigurationError


@pytest.fixture(scope="session")
def git_repo_path(tmp_path_factory: TempPathFactory) -> Callable[[Path], Path]:
    def createFolder():
        p = tmp_path_factory.mktemp(str(uuid.uuid4()))
        assert p.exists()
        yield p
        try:
            yield p
        finally:
            print("finally")

    yield createFolder


def _run_cmd(cmd: str, **kwargs) -> str:
    result = subprocess.run(
        cmd, capture_output=True, check=True, shell=True, encoding="utf-8", **kwargs
    )
    assert result.returncode == 0
    return result.stdout.rstrip() if result.stdout else ""


@pytest.fixture()
def git_repository(git_repo_path: Callable[[Path], Path]) -> Callable[[], str]:
    def createGitRepo():
        cwd_ = next(git_repo_path())
        _run_cmd(
            "git init; git config user.name tester; git config user.email tester@test.com",
            cwd=cwd_,
        )
        _run_cmd(
            "touch initial_file.txt; git add .; git commit -m 'initial commit';",
            cwd=cwd_,
        )
        yield f"file://localhost{cwd_}"

    yield createGitRepo


@pytest.fixture()
def git_config(git_repository: Callable[[], str]) -> Dict[str, Any]:
    cfg = {
        "main": {
            "synced_via_tags": False,
            "watched_git_repositories": [
                {
                    "id": "test-repo-0",
                    "url": str(next(git_repository())),
                    "branch": "pytestMockBranch",
                    "tags": "",
                    "pull_only_files": False,
                    "paths": [],
                    "username": "",
                    "password": "",
                }
            ],
        }
    }
    yield cfg


@pytest.fixture()
def git_config_two_repos_synced_same_tag_regex(
    git_repository: Callable[[], str]
) -> Dict[str, Any]:
    cfg = {
        "main": {
            "synced_via_tags": True,
            "watched_git_repositories": [
                {
                    "id": "test-repo-" + str(i),
                    "url": str(next(git_repository())),
                    "branch": "pytestMockBranch",
                    "tags": "^staging_.+",
                    "pull_only_files": False,
                    "paths": [],
                    "username": "",
                    "password": "",
                }
                for i in range(2)
            ],
        }
    }
    yield cfg


async def test_git_url_watcher_tag_sync(
    event_loop,
    git_config_two_repos_synced_same_tag_regex: Dict[str, Any],
    git_repo_path: Path,
):
    REPO_ID = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["id"]
    BRANCH = git_config_two_repos_synced_same_tag_regex["main"][
        "watched_git_repositories"
    ][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    assert git_config_two_repos_synced_same_tag_regex["main"]["synced_via_tags"]
    git_watcher = git_url_watcher.GitUrlWatcher(
        git_config_two_repos_synced_same_tag_regex
    )

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
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
        print(repo["url"])
        _run_cmd(
            f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest: I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
            cwd=repo["url"].replace("file://localhost", ""),
        )
    await git_watcher.cleanup()
    assert git_url_watcher._check_if_tag_on_branch(LOCAL_PATH, VALID_TAG, BRANCH)


async def test_git_url_watcher_find_new_file(loop, git_config: Dict[str, Any]):
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=LOCAL_PATH,
    )
    # we should have some changes here now
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_succeeds(
    loop, git_config: Dict[str, Any]
):
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    _run_cmd(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=LOCAL_PATH,
    )
    assert git_url_watcher._check_if_tag_on_branch(LOCAL_PATH, VALID_TAG, BRANCH)

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_raises_if_branch_doesnt_exist(
    loop, git_config: Dict[str, Any]
):
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    with pytest.raises(CmdLineError):  # Assert that the branch doesn't exist
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
            LOCAL_PATH, VALID_TAG, "nonexistingBranch"
        )

    await git_watcher.cleanup()


async def test_git_url_watcher_find_tag_on_branch_fails_if_tag_not_found(
    loop, git_config: Dict[str, Any], git_repo_path: Path
):
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    with pytest.raises(CmdLineError):
        init_result = await git_watcher.init()

    # add the a file, commit, and tag
    VALID_TAG = "staging_z1stvalid"
    TESTFILE_NAME = "testfile.csv"
    _run_cmd(
        f"touch {TESTFILE_NAME}; git add .; git commit -m 'pytest - I added {TESTFILE_NAME}'; git tag {VALID_TAG};",
        cwd=LOCAL_PATH,
    )
    assert not git_url_watcher._check_if_tag_on_branch(
        git_repo_path, "invalid_tag", BRANCH
    )

    await git_watcher.cleanup()


@pytest.fixture()
def git_config_pull_only_files(git_config: Dict[str, Any]) -> Dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["pull_only_files"] = True
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    return git_config


async def test_git_url_watcher_pull_only_selected_files(
    loop, git_config_pull_only_files: Dict[str, Any], git_repo_path: Path
):
    REPO_ID = git_config_pull_only_files["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config_pull_only_files["main"]["watched_git_repositories"][0]["branch"]
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    git_watcher = git_url_watcher.GitUrlWatcher(git_config_pull_only_files)
    # the file does not exist yet
    with pytest.raises(CmdLineError):
        init_result = await git_watcher.init()

    # add the file
    _run_cmd(
        "touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv';",
        cwd=LOCAL_PATH,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=LOCAL_PATH,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    # now modify theonefile.csv
    _run_cmd(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv';",
        cwd=LOCAL_PATH,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    await git_watcher.cleanup()


@pytest.fixture()
def git_config_pull_only_files_tags(git_config: Dict[str, Any]) -> Dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["pull_only_files"] = True
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    git_config["main"]["watched_git_repositories"][0]["tags"] = "^staging_.+$"
    return git_config


async def test_git_url_watcher_pull_only_selected_files_tags(
    loop, git_config_pull_only_files_tags: Dict[str, Any], git_repo_path: Path
):
    LOCAL_PATH = git_config["main"]["watched_git_repositories"][0]["url"].replace(
        "file://localhost", ""
    )
    REPO_ID = git_config_pull_only_files_tags["main"]["watched_git_repositories"][0][
        "id"
    ]
    BRANCH = git_config_pull_only_files_tags["main"]["watched_git_repositories"][0][
        "branch"
    ]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_pull_only_files_tags)

    # the file does not exist yet
    with pytest.raises(ConfigurationError):
        init_result = await git_watcher.init()

    # add the file
    VALID_TAG = "staging_z1stvalid"
    _run_cmd(
        f"touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=LOCAL_PATH,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{VALID_TAG}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file'",
        cwd=LOCAL_PATH,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    time.sleep(1.1)
    # now modify theonefile.csv
    _run_cmd(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'",
        cwd=LOCAL_PATH,
    )
    time.sleep(1.1)
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    INVALID_TAG = "v3.4.5"
    _run_cmd(
        f"git tag {INVALID_TAG}",
        cwd=LOCAL_PATH,
    )
    time.sleep(1.1)
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    NEW_VALID_TAG = "staging_g2ndvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG}",
        cwd=LOCAL_PATH,
    )
    time.sleep(1.1)
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{NEW_VALID_TAG}:{git_sha}"}

    NEW_VALID_TAG_ON_SAME_SHA = "staging_a3rdvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=LOCAL_PATH,
    )
    time.sleep(1.1)
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=LOCAL_PATH)
    assert change_results[REPO_ID].split(":")[-1] == git_sha

    # Check that tags are sorted in correct order, by tag time, not alphabetically
    assert len(git_watcher.watched_repos) == 1
    NEW_VALID_TAG_ON_SAME_SHA = "staging_z4thvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=LOCAL_PATH,
    )
    NEW_VALID_TAG_ON_NEW_SHA = "staging_h5thvalid"  # This name is intentionally "in between" the previous tags when alphabetically sorted
    _run_cmd(
        f"echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv'; git tag {NEW_VALID_TAG_ON_NEW_SHA}",
        cwd=LOCAL_PATH,
    )
    time.sleep(1.1)
    # we should have a change here
    change_results = await git_watcher.check_for_changes()
    latestTag = await git_url_watcher._git_get_latest_matching_tag(
        git_watcher.watched_repos[0].directory, git_watcher.watched_repos[0].tags
    )
    assert latestTag == NEW_VALID_TAG_ON_NEW_SHA
    #
    await git_watcher.cleanup()
