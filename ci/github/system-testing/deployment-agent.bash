#!/bin/bash
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
set -euo pipefail
IFS=$'\n\t'

install() {
    bash ci/helpers/ensure_python_pip.bash
    make devenv
    source .venv/bin/activate
    pip install -r requirements/dev.txt
    make build
    pip list -v
    docker images
    # use the config file for testing not the default
    cp tests/mocks/valid_system_test_config.yaml deployment_config.default.yaml
    make up-prod
    docker service ls
}

test() {
    source .venv/bin/activate
    make test-ci-system
    deactivate
}

# Check if the function exists (bash specific)
if declare -f "$1" > /dev/null
then
  # call arguments verbatim
  "$@"
else
  # Show a helpful error
  echo "'$1' is not a known function name" >&2
  exit 1
fi
