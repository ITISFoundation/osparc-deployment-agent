import re
import sys
from pathlib import Path

from setuptools import find_packages, setup

CURRENT_DIR = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent


def read_reqs(reqs_path: Path):
    return re.findall(r"(^[^#-][\w]+[-~>=<.\w]+)", reqs_path.read_text(), re.MULTILINE)


# -----------------------------------------------------------------

INSTALL_REQUIREMENTS = tuple(read_reqs(CURRENT_DIR / "requirements" / "_base.txt"))

TEST_REQUIREMENTS = tuple(read_reqs(CURRENT_DIR / "requirements" / "_test.txt"))

SETUP = dict(
    name="simcore-service-deployment-agent",
    version="0.10.0",
    author="Dustin Kaiser (mrnicegyu11)",
    description="Agent that automatically deploys oSparc services in a swarm",
    packages=find_packages(where="src"),
    package_dir={
        "": "src",
    },
    include_package_data=True,
    package_data={
        "": [
            "config/*.y*ml",
            "oas3/v0/*.y*ml",
            "oas3/v0/components/schemas/*.y*ml",
            "data/*.json",
            "templates/**/*.html",
        ]
    },
    entry_points={
        "console_scripts": [
            "simcore-service-deployment-agent = simcore_service_deployment_agent.cli:main",
        ]
    },
    python_requires=">=3.9",
    install_requires=INSTALL_REQUIREMENTS,
    tests_require=TEST_REQUIREMENTS,
    setup_requires=["pytest-runner"],
)

if __name__ == "__main__":
    setup(**SETUP)
