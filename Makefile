.DEFAULT_GOAL := help
# HELPER Makefile that countains all the recipe that will be used by every services. Please include it in your Makefile if you add a new service
SHELL := /bin/bash

MAKE_C := $(MAKE) --no-print-directory --directory

# Operating system
ifeq ($(filter Windows_NT,$(OS)),)
IS_WSL  := $(if $(findstring Microsoft,$(shell uname -a)),WSL,)
IS_OSX  := $(filter Darwin,$(shell uname -a))
IS_LINUX:= $(if $(or $(IS_WSL),$(IS_OSX)),,$(filter Linux,$(shell uname -a)))
endif

IS_WIN  := $(strip $(if $(or $(IS_LINUX),$(IS_OSX),$(IS_WSL)),,$(OS)))
$(if $(IS_WIN),$(error Windows is not supported in all recipes. Use WSL instead. Follow instructions in README.md),)

# version control
export VCS_URL := $(shell git config --get remote.origin.url)
export VCS_REF := $(shell git rev-parse --short HEAD)
export VCS_STATUS_CLIENT := $(if $(shell git status -s),'modified/untracked','clean')
export BUILD_DATE := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

# APP version
APP_NAME := $(notdir $(shell pwd))
export APP_VERSION := $(shell cat VERSION)

# version tags
export DOCKER_IMAGE_TAG ?= latest
export DOCKER_REGISTRY  ?= itisfoundation


# Internal VARIABLES ------------------------------------------------
TEMP_COMPOSE = .stack.${STACK_NAME}.yaml
TEMP_COMPOSE-devel = .stack.${STACK_NAME}.devel.yml
DEPLOYMENT_AGENT_CONFIG = deployment_config.yaml


.PHONY: help

help: ## help on rule's targets
ifeq ($(IS_WIN),)
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
else
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
endif

## DOCKER BUILD -------------------------------
#
# - all builds are inmediatly tagged as 'local/{service}:${BUILD_TARGET}' where BUILD_TARGET='development', 'production', 'cache'
# - only production and cache images are released (i.e. tagged pushed into registry)
#
SWARM_HOSTS = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),NUL,/dev/null))

define _docker_compose_build
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
$(if $(findstring -x,$@),\
	docker buildx bake --file docker-compose-build.yml;,\
	docker-compose -f docker-compose-build.yml build $(if $(findstring -nc,$@),--no-cache,) --parallel\
)
endef

.PHONY: build build-nc rebuild build-devel build-devel-nc build-devel-kit build-devel-x build-cache build-cache-kit build-cache-x build-cache-nc build-kit build-x
build build-kit build-x build-devel build-devel-kit build-devel-x: ## Builds $(APP_NAME) image
	@$(if $(findstring -kit,$@),export DOCKER_BUILDKIT=1;export COMPOSE_DOCKER_CLI_BUILD=1;,) \
	$(_docker_compose_build)


.PHONY: up
up: .init ${DEPLOYMENT_AGENT_CONFIG} ${TEMP_COMPOSE} ## Deploys or updates current stack "$(STACK_NAME)" using replicas=X (defaults to 1)
	@docker stack deploy --compose-file ${TEMP_COMPOSE} $(STACK_NAME)

.PHONY: up-devel
up-devel: .init ${DEPLOYMENT_AGENT_CONFIG} ${TEMP_COMPOSE-devel} ## Deploys or updates current stack "$(STACK_NAME)" using replicas=X (defaults to 1)
	@docker stack deploy --compose-file ${TEMP_COMPOSE-devel} $(STACK_NAME)

.PHONY: down
down: ## Stops and remove stack from swarm
	-@docker stack rm $(STACK_NAME)

.PHONY: push
push: ## Pushes service to the registry.
	docker push ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}
	docker tag ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG} ${DOCKER_REGISTRY}/deployment-agent:latest
	docker push ${DOCKER_REGISTRY}/$(APP_NAME):latest

.PHONY: pull
pull: ## Pulls service from the registry.
	docker pull ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}

.PHONY: config
config: ${DEPLOYMENT_AGENT_CONFIG} ## Create an initial configuration file.

.PHONY: install-dev install-prod install-ci

install-dev install-prod install-ci: _check_venv_active ## install app in development/production or CI mode
	# installing in $(subst install-,,$@) mode
	pip-sync requirements/$(subst install-,,$@).txt

.PHONY: test-dev-unit test-ci-unit test-dev-integration test-ci-integration test-dev

test-dev-unit test-ci-unit: _check_venv_active
	# targets tests/unit folder
	@make --no-print-directory _run-$(subst -unit,,$@) target=$(CURDIR)/tests/unit

test-dev-integration test-ci-integration:
	# targets tests/integration folder using local/$(image-name):production images
	@export DOCKER_REGISTRY=local; \
	export DOCKER_IMAGE_TAG=production; \
	make --no-print-directory _run-$(subst -integration,,$@) target=$(CURDIR)/tests/integration


test-dev: test-dev-unit test-dev-integration ## runs unit and integration tests for development (e.g. w/ pdb)

## PYTHON -------------------------------
.PHONY: pylint

PY_PIP = $(if $(IS_WIN),cd .venv/Scripts && pip.exe,.venv/bin/pip3)

pylint: ## Runs python linter framework's wide
	# See exit codes and command line https://pylint.readthedocs.io/en/latest/user_guide/run.html#exit-codes
	# TODO: NOT windows friendly
	/bin/bash -c "pylint --jobs=0 --rcfile=.pylintrc $(strip $(shell find src -iname '*.py' \
											-not -path "*egg*" \
											-not -path "*migration*" \
											-not -path "*contrib*" \
											-not -path "*-sdk/python*" \
											-not -path "*generated_code*" \
											-not -path "*datcore.py" \
											-not -path "*web/server*"))"

.PHONY: devenv devenv-all

.venv:
	python3 -m venv $@
	$@/bin/pip3 install --upgrade \
		pip \
		wheel \
		setuptools

devenv: .venv ## create a python virtual environment with dev tools (e.g. linters, etc)
	$</bin/pip3 --quiet install -r requirements/devenv.txt
	# Installing pre-commit hooks in current .git repo
	@$</bin/pre-commit install
	@echo "To activate the venv, execute 'source .venv/bin/activate'"

.env: .env-devel ## creates .env file from defaults in .env-devel
	$(if $(wildcard $@), \
	@echo "WARNING #####  $< is newer than $@ ####"; diff -uN $@ $<; false;,\
	@echo "WARNING ##### $@ does not exist, cloning $< as $@ ############"; cp $< $@)


.vscode/settings.json: .vscode-template/settings.json
	$(info WARNING: #####  $< is newer than $@ ####)
	@diff -uN $@ $<
	@false

# Helpers -------------------------------------------------
${DEPLOYMENT_AGENT_CONFIG}:  deployment_config.template.yaml
	@set -o allexport; \
	source $(realpath $(CURDIR)/../../repo.config); \
	set +o allexport; \
	envsubst < $< > $@


docker-compose-configs = $(wildcard docker-compose*.yml)

.PHONY: ${TEMP_COMPOSE}

${TEMP_COMPOSE}: .env $(docker-compose-configs)
	@docker-compose --file docker-compose.yml --log-level=ERROR config > $@

.PHONY: ${TEMP_COMPOSE-devel}
${TEMP_COMPOSE-devel}: .env $(docker-compose-configs)
	@docker-compose --file docker-compose.yml --file docker-compose.devel.yaml --log-level=ERROR config > $@

## CLEAN -------------------------------

.PHONY: clean clean-images clean-venv clean-all clean-more

_git_clean_args := -dxf -e .vscode -e TODO.md -e .venv -e .python-version
_running_containers = $(shell docker ps -aq)

.check-clean:
	@git clean -n $(_git_clean_args)
	@echo -n "Are you sure? [y/N] " && read ans && [ $${ans:-N} = y ]
	@echo -n "$(shell whoami), are you REALLY sure? [y/N] " && read ans && [ $${ans:-N} = y ]

clean-venv: devenv ## Purges .venv into original configuration
	# Cleaning your venv
	.venv/bin/pip-sync --quiet $(CURDIR)/requirements/devenv.txt
	@pip list

clean-hooks: ## Uninstalls git pre-commit hooks
	@-pre-commit uninstall 2> /dev/null || rm .git/hooks/pre-commit

clean: .check-clean ## cleans all unversioned files in project and temp files create by this makefile
	# Cleaning unversioned
	@git clean $(_git_clean_args)

clean-more: ## cleans containers and unused volumes
	# stops and deletes running containers
	@$(if $(_running_containers), docker rm -f $(_running_containers),)
	# pruning unused volumes
	docker volume prune --force

clean-images: ## removes all created images
	# Cleaning all service images
	-$(foreach service,$(SERVICES_LIST)\
		,docker image rm -f $(shell docker images */$(service):* -q);)

clean-all: clean clean-more clean-images clean-hooks # Deep clean including .venv and produced images
	-rm -rf .venv


.PHONY: reset
reset: ## restart docker daemon (LINUX ONLY)
	sudo systemctl restart docker

.PHONY: autoformat
autoformat: ## runs black python formatter on this service's code. Use AFTER make install-*
	# sort imports
	@python3 -m isort --verbose \
		--atomic \
		--recursive \
		--skip-glob */client-sdk/* \
		--skip-glob */migration/* \
		$(CURDIR)
	# auto formatting with black
	@python3 -m black --verbose \
		--exclude "/(\.eggs|\.git|\.hg|\.mypy_cache|\.nox|\.tox|\.venv|\.svn|_build|buck-out|build|dist|migration|client-sdk|generated_code)/" \
		$(CURDIR)


.PHONY: mypy
mypy: $(REPO_BASE_DIR)/scripts/mypy.bash $(REPO_BASE_DIR)/mypy.ini ## runs mypy python static type checker on this services's code. Use AFTER make install-*
	@$(REPO_BASE_DIR)/scripts/mypy.bash src

.PHONY: version-patch version-minor version-major
version-patch: ## commits version with bug fixes not affecting the cookiecuter config
	$(_bumpversion)
version-minor: ## commits version with backwards-compatible API addition or changes (i.e. can replay)
	$(_bumpversion)
version-major: ## commits version with backwards-INcompatible addition or changes
	$(_bumpversion)


#
# SUBTASKS
#

.PHONY: _check_python_version _check_venv_active

_check_python_version:
	# Checking that runs with correct python version
	@python3 -c "import sys; assert sys.version_info[:2]==(3,9), f'Expected python 3.9, got {sys.version_info}'"


_check_venv_active: _check_python_version
	# checking whether virtual environment was activated
	@python3 -c "import sys; assert sys.base_prefix!=sys.prefix"


define _bumpversion
	# upgrades as $(subst version-,,$@) version, commits and tags
	@bump2version --verbose --list $(subst version-,,$@)
endef

.PHONY: _run-test-dev _run-test-ci

TEST_TARGET := $(if $(target),$(target),$(CURDIR)/tests/unit)

_run-test-dev: _check_venv_active
	# runs tests for development (e.g w/ pdb)
	pytest -vv --exitfirst --failed-first --durations=10 --pdb $(TEST_TARGET)


_run-test-ci: _check_venv_active
	# runs tests for CI (e.g. w/o pdb but w/ converage)
	pytest --cov=$(APP_PACKAGE_NAME) --durations=10 --cov-append --color=yes --cov-report=term-missing --cov-report=xml --cov-config=.coveragerc -v -m "not travis" $(TEST_TARGET)
