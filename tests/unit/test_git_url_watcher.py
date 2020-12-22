# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=bare-except

import subprocess
from asyncio import Future
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from yarl import URL

from simcore_service_deployment_agent import git_url_watcher
from simcore_service_deployment_agent.cmd_utils import CmdLineError


def _list_valid_configs():
    return [
        "valid_git_config.yaml",
        "valid_git_config_path.yaml",
        "valid_git_config_staging.yaml",
        "valid_git_config_staging_tags.yaml",
    ]


@pytest.fixture(scope="session", params=_list_valid_configs())
def valid_git_config(mocks_dir: Path, request) -> Dict[str, Any]:
    with Path(mocks_dir / request.param).open() as fp:
        return yaml.safe_load(fp)


TAG = "1.2.3"
SHA = "asdhjfs"


@pytest.fixture()
def mock_git_fcts(mocker, valid_git_config) -> Dict[str, Any]:
    mock_git_fcts = {
        "_git_get_latest_matching_tag": mocker.patch.object(
            git_url_watcher, "_git_get_latest_matching_tag", return_value=TAG
        ),
        "_git_get_current_matching_tag": mocker.patch.object(
            git_url_watcher, "_git_get_current_matching_tag", return_value=TAG
        ),
        "_git_get_current_sha": mocker.patch.object(
            git_url_watcher, "_git_get_current_sha", return_value=SHA
        ),
        "_git_diff_filenames": mocker.patch.object(
            git_url_watcher, "_git_diff_filenames", return_value=""
        ),
    }
    yield mock_git_fcts


@pytest.fixture()
def git_repo_path(tmpdir: Path) -> Path:
    p = tmpdir.mkdir("test_git_repo")
    assert p.exists()
    return p


def _run_cmd(cmd: str, **kwargs) -> str:
    result = subprocess.run(
        cmd, capture_output=True, check=True, shell=True, encoding="utf-8", **kwargs
    )
    assert result.returncode == 0
    return result.stdout.rstrip() if result.stdout else ""


@pytest.fixture()
def git_repository(git_repo_path: Path) -> str:
    _run_cmd(
        "git init; git config user.name tester; git config user.email tester@test.com",
        cwd=git_repo_path,
    )
    _run_cmd(
        "touch initial_file.txt; git add .; git commit -m 'initial commit';",
        cwd=git_repo_path,
    )

    yield f"file://localhost{git_repo_path}"


@pytest.fixture()
def git_config(git_repository: str) -> Dict[str, Any]:
    cfg = {
        "main": {
            "watched_git_repositories": [
                {
                    "id": "test-repo-1",
                    "url": str(git_repository),
                    "branch": "master",
                    "tags": "",
                    "pull_only_files": False,
                    "paths": [],
                    "username": "fakeuser",
                    "password": "fakepassword",
                }
            ]
        }
    }
    yield cfg


async def test_git_url_watcher_find_new_file(
    git_config: Dict[str, Any], git_repo_path: Path
):
    REPO_ID = git_config["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config)
    init_result = await git_watcher.init()

    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=git_repo_path,
    )
    # we should have some changes here now
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    await git_watcher.cleanup()


@pytest.fixture()
def git_config_pull_only_files(git_config: Dict[str, Any]) -> Dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["pull_only_files"] = True
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    return git_config


async def test_git_url_watcher_pull_only_selected_files(
    git_config_pull_only_files: Dict[str, Any], git_repo_path: Path
):
    REPO_ID = git_config_pull_only_files["main"]["watched_git_repositories"][0]["id"]
    BRANCH = git_config_pull_only_files["main"]["watched_git_repositories"][0]["branch"]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_pull_only_files)
    # the file does not exist yet
    with pytest.raises(CmdLineError):
        init_result = await git_watcher.init()

    # add the file
    _run_cmd(
        "touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv';",
        cwd=git_repo_path,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=git_repo_path,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    # now modify theonefile.csv
    _run_cmd(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv';",
        cwd=git_repo_path,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{git_sha}"}

    await git_watcher.cleanup()


@pytest.fixture()
def git_config_pull_only_files_tags(git_config: Dict[str, Any]) -> Dict[str, Any]:
    git_config["main"]["watched_git_repositories"][0]["pull_only_files"] = True
    git_config["main"]["watched_git_repositories"][0]["paths"] = ["theonefile.csv"]
    git_config["main"]["watched_git_repositories"][0]["tags"] = "^staging_.+$"
    return git_config


async def test_git_url_watcher_pull_only_selected_files_tags(
    git_config_pull_only_files_tags: Dict[str, Any], git_repo_path: Path
):
    REPO_ID = git_config_pull_only_files_tags["main"]["watched_git_repositories"][0][
        "id"
    ]
    BRANCH = git_config_pull_only_files_tags["main"]["watched_git_repositories"][0][
        "branch"
    ]

    git_watcher = git_url_watcher.GitUrlWatcher(git_config_pull_only_files_tags)
    # the file does not exist yet
    with pytest.raises(CmdLineError):
        init_result = await git_watcher.init()

    # add the file
    VALID_TAG = "staging_z1stvalid"
    _run_cmd(
        f"touch theonefile.csv; git add .; git commit -m 'I added theonefile.csv'; git tag {VALID_TAG};",
        cwd=git_repo_path,
    )
    # expect to work now
    init_result = await git_watcher.init()
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert init_result == {REPO_ID: f"{REPO_ID}:{BRANCH}:{VALID_TAG}:{git_sha}"}

    # there was no changes
    assert not await git_watcher.check_for_changes()

    # now add a file in the repo
    _run_cmd(
        "touch my_file.txt; git add .; git commit -m 'I added a file';",
        cwd=git_repo_path,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    # now modify theonefile.csv
    _run_cmd(
        "echo 'blahblah' >> theonefile.csv; git add .; git commit -m 'I modified theonefile.csv';",
        cwd=git_repo_path,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results
    INVALID_TAG = "v3.4.5"
    _run_cmd(
        f"git tag {INVALID_TAG};",
        cwd=git_repo_path,
    )
    # we should have no change here
    change_results = await git_watcher.check_for_changes()
    assert not change_results

    NEW_VALID_TAG = "staging_g2ndvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG};",
        cwd=git_repo_path,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert change_results == {REPO_ID: f"{REPO_ID}:{BRANCH}:{NEW_VALID_TAG}:{git_sha}"}

    NEW_VALID_TAG_ON_SAME_SHA = "staging_a3rdvalid"
    _run_cmd(
        f"git tag {NEW_VALID_TAG_ON_SAME_SHA};",
        cwd=git_repo_path,
    )
    # now there should be changes
    change_results = await git_watcher.check_for_changes()
    # get new sha
    git_sha = _run_cmd("git rev-parse --short HEAD", cwd=git_repo_path)
    assert change_results == {
        REPO_ID: f"{REPO_ID}:{BRANCH}:{NEW_VALID_TAG_ON_SAME_SHA}:{git_sha}"
    }

    await git_watcher.cleanup()
