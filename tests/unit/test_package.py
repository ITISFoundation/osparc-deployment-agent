# pylint:disable=wildcard-import
# pylint:disable=unused-import
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name

import os
import re
import subprocess
from pathlib import Path

import pytest

from simcore_service_deployment_agent.cli import main


@pytest.fixture(scope="session")
def pylintrc(root_dir: Path) -> Path:
    pylintrc = root_dir / ".pylintrc"
    assert pylintrc.exists()
    return pylintrc


def test_run_pylint(pylintrc: Path, package_dir: Path):
    command = f"pylint --jobs={0} --rcfile {pylintrc} -v {package_dir}".split(" ")
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        std_out, _ = process.communicate()
        if process.returncode != 0:
            assert (
                False
            ), f"Pylint failed with error\nExit code {process.returncode}\n{std_out.decode('utf-8')}"


def test_main():  # pylint: disable=unused-variable
    with pytest.raises(SystemExit) as excinfo:
        main("--help".split())

    assert excinfo.value.code == 0


def test_no_pdbs_in_place(package_dir: Path):
    # TODO: add also test_dir excluding this function!?
    # TODO: it can be commented!
    MATCH = re.compile(r"pdb.set_trace()")
    EXCLUDE = ["__pycache__", ".git"]
    for root, dirs, files in os.walk(package_dir):
        for name in files:
            if name.endswith(".py"):
                pypth = Path(root) / name
                code = pypth.read_text()
                found = MATCH.findall(code)
                assert not found, "pbd.set_trace found in %s" % pypth
        dirs[:] = [d for d in dirs if d not in EXCLUDE]
