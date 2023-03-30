ARG PYTHON_VERSION="3.10.0"
FROM python:${PYTHON_VERSION}-slim-buster as base

#
#  USAGE:
#     cd sercices/deployment-agent
#     docker build -f Dockerfile -t deployment-agent:prod --target production ../../
#     docker run deployment-agent:prod
#
#  REQUIRED: context expected at ``osparc-simcore/`` folder because we need access to osparc-simcore/packages

LABEL maintainer=mrnicegyu11

RUN set -eux; \
  apt-get update; \
  apt-get install -y --no-install-recommends gosu; \
  rm -rf /var/lib/apt/lists/*; \
  # verify that the binary works
  gosu nobody true

# simcore-user uid=8004(scu) gid=8004(scu) groups=8004(scu)
ENV SC_USER_ID=8004 \
  SC_USER_NAME=scu \
  SC_BUILD_TARGET=base \
  SC_BOOT_MODE=default

RUN adduser \
  --uid ${SC_USER_ID} \
  --disabled-password \
  --gecos "" \
  --shell /bin/sh \
  --home /home/${SC_USER_NAME} \
  ${SC_USER_NAME}

# Sets utf-8 encoding for Python et al
ENV LANG=C.UTF-8

# Turns off writing .pyc files; superfluous on an ephemeral container.
ENV PYTHONDONTWRITEBYTECODE=1 \
  VIRTUAL_ENV=/home/scu/.venv

# Ensures that the python and pip executables used
# in the image will be those from our virtualenv.
ENV PATH="${VIRTUAL_ENV}/bin:$PATH"

EXPOSE 8888
EXPOSE 3000


# necessary tools for running deployment-agent
RUN apt-get update &&\
  apt-get install -y --no-install-recommends \
  bash \
  curl \
  gawk \
  git \
  gpg \
  lsb-release \
  make \
  gettext \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# -------------------------- Build stage -------------------
# Installs build/package management tools and third party dependencies
#
# + /build             WORKDIR
#

FROM base as build

ENV SC_BUILD_TARGET=build

RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
  echo \
  "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
  apt-get update && \
  apt-get install -y --no-install-recommends \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-compose-plugin

RUN apt-get update &&\
  apt-get install -y --no-install-recommends \
  build-essential \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*


# NOTE: python virtualenv is used here such that installed packages may be moved to production image easily by copying the venv
RUN python -m venv "${VIRTUAL_ENV}"

ARG DOCKER_COMPOSE_VERSION="1.27.4"
RUN pip --no-cache-dir install --upgrade \
  pip  \
  wheel \
  setuptools \
  docker-compose~=${DOCKER_COMPOSE_VERSION}

WORKDIR /build

# All SC_ variables are customized
ENV SC_PIP pip3 --no-cache-dir
ENV SC_BUILD_TARGET base

COPY --chown=scu:scu requirements/_base.txt .
# install base 3rd party dependencies (NOTE: this speeds up devel mode)
RUN pip --no-cache-dir install -r _base.txt


# --------------------------Cache stage -------------------
# CI in master buils & pushes this target to speed-up image build
#
#  + /build
#    + services/sidecar [scu:scu] WORKDIR
#
FROM build as cache

ENV SC_BUILD_TARGET=cache

COPY --chown=scu:scu . /build/services/deployment-agent

WORKDIR /build/services/deployment-agent

RUN pip --no-cache-dir install -r requirements/prod.txt \
  && pip3 --no-cache-dir list -v

# --------------------------Production stage -------------------
# Final cleanup up to reduce image size and startup setup
# Runs as scu (non-root user)
#
#  + /home/scu     $HOME = WORKDIR
#    + services/sidecar [scu:scu]
#
FROM base as production

ENV SC_BUILD_TARGET=production \
  SC_BOOT_MODE=production

ENV PYTHONOPTIMIZE=TRUE

WORKDIR /home/scu

# bring installed package without build tools
COPY --from=cache --chown=scu:scu ${VIRTUAL_ENV} ${VIRTUAL_ENV}
# copy docker entrypoint and boot scripts
COPY --chown=scu:scu docker services/deployment-agent/docker
RUN chmod +x services/deployment-agent/docker/*.sh

HEALTHCHECK --interval=30s \
  --timeout=60s \
  --start-period=30s \
  --retries=3 \
  CMD python3 /home/scu/services/deployment-agent/docker/healthcheck.py 'http://localhost:8888/v0/'

ENTRYPOINT [ "/bin/sh", "services/deployment-agent/docker/entrypoint.sh" ]
CMD ["/bin/sh", "services/deployment-agent/docker/boot.sh"]


# --------------------------Development stage -------------------
# Source code accessible in host but runs in container
# Runs as scu with same gid/uid as host
# Placed at the end to speed-up the build if images targeting production
#
#  + /devel         WORKDIR
#    + services  (mounted volume)
#
FROM build as development

ENV SC_BUILD_TARGET=development \
  SC_DEVEL_MOUNT=/devel/services/deployment-agent

WORKDIR /devel

RUN chown -R scu:scu "${VIRTUAL_ENV}"

ENTRYPOINT [ "/bin/sh", "services/deployment-agent/docker/entrypoint.sh" ]
CMD ["/bin/sh", "services/deployment-agent/docker/boot.sh"]
