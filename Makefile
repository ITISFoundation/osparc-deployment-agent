include scripts/common.Makefile

# Variables based on conventions
APP_NAME          = deployment-agent
APP_CLI_NAME      = simcore-service-$(APP_NAME)
APP_PACKAGE_NAME  = $(subst -,_,$(APP_CLI_NAME))
APP_VERSION      := $(shell cat VERSION)
SRC_DIR           = $(abspath $(CURDIR)/src/$(APP_PACKAGE_NAME))
STACK_NAME        = $(APP_NAME)
# Internal VARIABLES ------------------------------------------------
DEPLOYMENT_AGENT_CONFIG = .deployment_config.yaml

export DOCKER_IMAGE_TAG ?= latest
export DOCKER_REGISTRY  ?= itisfoundation

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
docker-compose-configs = $(wildcard docker-compose*.yml)
get_my_ip := $(shell hostname --all-ip-addresses | cut --delimiter=" " --fields=1)

.stack.${STACK_NAME}-prod.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:production' to $@
	@export DOCKER_REGISTRY=local \
	export DOCKER_IMAGE_TAG=development; \
	docker-compose --file docker-compose.yml --log-level=ERROR config > $@

.stack.${STACK_NAME}-devel.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:development' to $@
	@export DOCKER_REGISTRY=local \
	export DOCKER_IMAGE_TAG=development; \
	docker-compose --file docker-compose.yml --file docker-compose.devel.yaml --log-level=ERROR config > $@

.stack.${STACK_NAME}-version.yml: .env $(docker-compose-configs)
	# Creating config for stack with '$(DOCKER_REGISTRY)/{service}:${DOCKER_IMAGE_TAG}' to $@
	@docker-compose --file docker-compose.yml --log-level=ERROR config > $@

.PHONY: up
up-prod up-devel up-version: .init-swarm ${DEPLOYMENT_AGENT_CONFIG}  ## Deploys or updates current stack "$(STACK_NAME)"
	@$(MAKE) .stack.${STACK_NAME}$(subst up,,$@).yml
	@docker stack deploy --with-registry-auth --compose-file .stack.$(STACK_NAME)$(subst up,,$@).yml $(STACK_NAME)

.PHONY: down
down: ## Stops and remove stack from swarm
	-@docker stack rm $(STACK_NAME)

leave: ## Forces to stop all services, networks, etc by the node leaving the swarm
	-docker swarm leave -f

.PHONY: .init-swarm
.init-swarm:
	# Ensures swarm is initialized
	$(if $(SWARM_HOSTS),,docker swarm init --advertise-addr=$(get_my_ip))


## DOCKER TAGS  -------------------------------

.PHONY: tag-local tag-cache tag-version tag-latest

tag-local: ## Tags version '${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}' images as 'local/$(APP_NAME):production'
	# Tagging all '${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}' as 'local/$(APP_NAME):production'
	docker tag ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG} local/$(APP_NAME):production

tag-version: ## Tags 'local/$(APP_NAME):production' images as versioned '${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}'
	# Tagging all 'local/$(APP_NAME):production' as '${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}'
	docker tag local/$(APP_NAME):production ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}


## DOCKER PULL/PUSH  -------------------------------
.PHONY: push-version
push-version: ## Pushes ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG} to the registry.
	docker push ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}
	docker tag ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG} ${DOCKER_REGISTRY}/$(APP_NAME):latest
	docker push ${DOCKER_REGISTRY}/$(APP_NAME):latest

.PHONY: pull-version
pull-version: ## Pulls ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG} service from the registry.
	docker pull ${DOCKER_REGISTRY}/$(APP_NAME):${DOCKER_IMAGE_TAG}


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
	source $(realpath $(CURDIR)/.env); \
	set +o allexport; \
	envsubst < $< > $@




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


## INFO -------------------------------

.PHONY: info info-images info-swarm  info-tools
info: ## displays setup information
	# setup info:
	@echo ' Detected OS          : $(IS_LINUX)$(IS_OSX)$(IS_WSL)$(IS_WIN)'
	@echo ' DOCKER_REGISTRY      : $(DOCKER_REGISTRY)'
	@echo ' DOCKER_IMAGE_TAG     : ${DOCKER_IMAGE_TAG}'
	@echo ' BUILD_DATE           : ${BUILD_DATE}'
	@echo ' VCS_* '
	@echo '  - URL                : ${VCS_URL}'
	@echo '  - REF                : ${VCS_REF}'
	@echo '  - (STATUS)REF_CLIENT : (${VCS_STATUS_CLIENT}) ${VCS_REF_CLIENT}'
	# dev tools version
	@echo ' make   : $(shell make --version 2>&1 | head -n 1)'
	@echo ' jq     : $(shell jq --version)'
	@echo ' awk    : $(shell awk -W version 2>&1 | head -n 1)'
	@echo ' python : $(shell python3 --version)'
	@echo ' node   : $(shell node --version 2> /dev/null || echo ERROR nodejs missing)'


define show-meta
	$(foreach iid,$(shell docker images */$(1):* -q | sort | uniq),\
		docker image inspect $(iid) | jq '.[0] | .RepoTags, .ContainerConfig.Labels, .Config.Labels';)
endef

info-images:  ## lists tags and labels of built images. To display one: 'make target=webserver info-images'
	@$(call show-meta,$(APP_NAME))

info-swarm: ## displays info about stacks and networks
ifneq ($(SWARM_HOSTS), )
	# Stacks in swarm
	@docker stack ls
	# Networks
	@docker network ls
endif
