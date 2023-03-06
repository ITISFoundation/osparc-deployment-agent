#
# These are COMMON target and recipes to Makefiles for **packages/ and services/**
#
# This file is included at the top of every Makefile
#
# $(CURDIR) in this file refers to the directory where this file is included
#
# SEE https://mattandre.ws/2016/05/makefile-inheritance/
#

#
# GLOBALS
#

# defaults
.DEFAULT_GOAL := help

# Use bash not sh
SHELL := /bin/bash

# Some handy flag variables
ifeq ($(filter Windows_NT,$(OS)),)
IS_WSL  := $(if $(findstring Microsoft,$(shell uname -a)),WSL,)
IS_OSX  := $(filter Darwin,$(shell uname -a))
IS_LINUX:= $(if $(or $(IS_WSL),$(IS_OSX)),,$(filter Linux,$(shell uname -a)))
endif
IS_WIN  := $(strip $(if $(or $(IS_LINUX),$(IS_OSX),$(IS_WSL)),,$(OS)))

$(if $(IS_WIN),\
$(error Windows is not supported in all recipes. Use WSL instead. Follow instructions in README.md),)

# version control
export VCS_URL       := $(shell git config --get remote.origin.url)
export VCS_REF       := $(shell git rev-parse --short HEAD)
VCS_STATUS_CLIENT:= $(if $(shell git status -s),'modified/untracked','clean')
export BUILD_DATE := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
REPO_BASE_DIR := $(shell git rev-parse --show-toplevel)

# virtual env
VENV_DIR      := $(abspath $(REPO_BASE_DIR)/.venv)

#
# SHORTCUTS
#

MAKE_C := $(MAKE) --no-print-directory --directory

#
# COMMON TASKS
#


.PHONY: help
# thanks to https://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
help:
	@echo "usage: make [target] ..."
	@echo ""
	@echo "Targets for '$(notdir $(CURDIR))':"
	@echo ""
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""



.PHONY: clean
_GIT_CLEAN_ARGS = -dxf -e .vscode -e .venv -e .python-version
clean: ## cleans all unversioned files in project and temp files create by this makefile
	# Cleaning unversioned
	@git clean -n $(_GIT_CLEAN_ARGS)
	@echo -n "Are you sure? [y/N] " && read ans && [ $${ans:-N} = y ]
	@echo -n "$(shell whoami), are you REALLY sure? [y/N] " && read ans && [ $${ans:-N} = y ]
	@git clean $(_GIT_CLEAN_ARGS)


.PHONY: info
inf%: ## displays basic info
	# system
	@echo ' OS               : $(IS_LINUX)$(IS_OSX)$(IS_WSL)$(IS_WIN)'
	@echo ' CURDIR           : ${CURDIR}'
	@echo ' NOW_TIMESTAMP    : ${NOW_TIMESTAMP}'
	@echo ' VCS_URL          : ${VCS_URL}'
	@echo ' VCS_REF          : ${VCS_REF}'
	# installed in .venv
	@pip list
	# package
	-@echo ' name         : ' $(shell python ${CURDIR}/setup.py --name)
	-@echo ' version      : ' $(shell python ${CURDIR}/setup.py --version)


.PHONY: codeformat
codeformat: ## runs all code formatters. Use AFTER make install-*
	@$(eval PYFILES=$(shell find $(CURDIR) -type f -name '*.py'))
	@pre-commit run pyupgrade --files $(PYFILES)
	@pre-commit run pycln --files $(PYFILES)
	@pre-commit run isort --files $(PYFILES)
	@pre-commit run black --files $(PYFILES)


.PHONY: pylint
pylint: $(REPO_BASE_DIR)/.pylintrc ## runs pylint (python linter) on src and tests folders
	@pylint --rcfile="$(REPO_BASE_DIR)/.pylintrc" -v $(CURDIR)/src $(CURDIR)/tests


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
