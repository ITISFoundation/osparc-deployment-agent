include scripts/common.Makefile

# Variables based on conventions
APP_NAME          = deployment-agent
APP_CLI_NAME      = simcore-service-$(APP_NAME)
APP_PACKAGE_NAME  = $(subst -,_,$(APP_CLI_NAME))
APP_VERSION      := $(shell cat VERSION)
SRC_DIR           = $(abspath $(CURDIR)/src/$(APP_PACKAGE_NAME))

# Internal VARIABLES ------------------------------------------------
TEMP_COMPOSE = .stack.${STACK_NAME}.yaml
TEMP_COMPOSE-devel = .stack.${STACK_NAME}.devel.yml
DEPLOYMENT_AGENT_CONFIG = deployment_config.yaml


## DOCKER BUILD -------------------------------
#
# - all builds are inmediatly tagged as 'local/{service}:${BUILD_TARGET}' where BUILD_TARGET='development', 'production', 'cache'
# - only production and cache images are released (i.e. tagged pushed into registry)
#
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

## DOCKER SWARM ----------------------------------
SWARM_HOSTS            = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),null,/dev/null))

.PHONY: up
up: .init-swarm ${DEPLOYMENT_AGENT_CONFIG} ${TEMP_COMPOSE} ## Deploys or updates current stack "$(STACK_NAME)" using replicas=X (defaults to 1)
	@docker stack deploy --compose-file ${TEMP_COMPOSE} $(STACK_NAME)

.PHONY: up-devel
up-devel: .init-swarm ${DEPLOYMENT_AGENT_CONFIG} ${TEMP_COMPOSE-devel} ## Deploys or updates current stack "$(STACK_NAME)" using replicas=X (defaults to 1)
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


.PHONY: .init-swarm
.init-swarm:
	# Ensures swarm is initialized
	$(if $(SWARM_HOSTS),,docker swarm init --advertise-addr=$(get_my_ip))

## TEST ---------------------------------

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

.PHONY: clean-venv clean-hooks

clean-venv: devenv ## Purges .venv into original configuration
	# Cleaning your venv
	.venv/bin/pip-sync --quiet $(CURDIR)/requirements/devenv.txt
	@pip list

clean-hooks: ## Uninstalls git pre-commit hooks
	@-pre-commit uninstall 2> /dev/null || rm .git/hooks/pre-commit


#
# SUBTASKS
#


.PHONY: _run-test-dev _run-test-ci

TEST_TARGET := $(if $(target),$(target),$(CURDIR)/tests/unit)

_run-test-dev: _check_venv_active
	# runs tests for development (e.g w/ pdb)
	pytest -vv --exitfirst --failed-first --durations=10 --pdb $(TEST_TARGET)


_run-test-ci: _check_venv_active
	# runs tests for CI (e.g. w/o pdb but w/ converage)
	pytest --cov=$(APP_PACKAGE_NAME) --durations=10 --cov-append --color=yes --cov-report=term-missing --cov-report=xml --cov-config=.coveragerc -v -m "not travis" $(TEST_TARGET)
