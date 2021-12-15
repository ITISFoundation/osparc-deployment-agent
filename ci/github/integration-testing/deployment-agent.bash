#!/bin/bash
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
set -o errexit  # abort on nonzero exitstatus
set -o nounset  # abort on unbound variable
set -o pipefail # don't hide errors within pipes
IFS=$'\n\t'

install() {
    # Due to ubuntu self hosted runners, this following comman needs to fail quietly and return zero
    bash ci/helpers/ensure_python_pip.bash 2>&1 || true
    make devenv
    source .venv/bin/activate
    make install-ci
    pip list -v
}

test() {
    source .venv/bin/activate
    make test-ci-integration
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
