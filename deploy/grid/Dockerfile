# One Edge Grid node, containerised.
#
# Why this image exists: every timing in this repo up to now was taken with all
# nodes as OS processes on one host, sharing one loopback interface and one
# network stack. A container gives each node its own network namespace, its own
# routable address on a real bridge, and a real interface to attach `tc netem`
# to. That removes the loopback shortcut. It does not turn one machine into
# four - see deploy/grid/README.md for exactly what this topology is and is not.
#
# Two build details, both of which cost real time to rediscover:
#
#   1. py-libp2p pins fastecdsa==2.3.2, which ships no wheel for CPython 3.12
#      and needs gmp.h to compile. Inside a Debian image the whole fix is
#      `apt-get install libgmp-dev` at build time plus libgmp10 at run time.
#      None of the no-sudo header extraction that setup.sh performs on the host
#      is needed here, which is the single biggest ergonomic win of the image.
#   2. iproute2 is installed only so the entrypoint can run `tc` for the
#      optional netem latency injection. If NET_ADMIN is not granted the
#      entrypoint records that the injection was skipped; it never pretends.
#
# Nothing secret is baked in. Node identities are bind-mounted read-only at run
# time, and the host virtualenv is never copied - the image builds its own
# wheels from deploy/grid/requirements-node.txt.
#
# Build from the REPO ROOT, not from this directory:
#   docker build -f deploy/grid/Dockerfile -t edgegrid-node:dev .

FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgmp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/grid/requirements-node.txt /tmp/requirements-node.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements-node.txt


FROM python:3.12-slim

# libgmp10 is the shared library the fastecdsa wheel links against; iproute2 is
# `tc` and `ip` for the entrypoint. build-essential deliberately does not
# survive into this stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgmp10 iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY deploy/grid/requirements-node.txt /tmp/requirements-node.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements-node.txt \
    && rm -rf /wheels /tmp/requirements-node.txt

WORKDIR /app
COPY edgegrid /app/edgegrid
COPY discovery /app/discovery
COPY deploy/grid/entrypoint.sh /usr/local/bin/eg-node-entrypoint
RUN chmod +x /usr/local/bin/eg-node-entrypoint

# edgegrid.config creates its results/data/figure directories on import. Left at
# their defaults they would be created under /app, inside the image's own tree,
# which is both surprising and useless in a throwaway container. Point them at
# /tmp so the image stays read-only in spirit and results are written on the
# host by the launcher instead.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    EDGEGRID_RESULTS_DIR=/tmp/edgegrid/results \
    EDGEGRID_DATA_DIR=/tmp/edgegrid/data \
    EDGEGRID_DA_DIR=/tmp/edgegrid/da \
    EDGEGRID_FIGURES_DIR=/tmp/edgegrid/figures

ENTRYPOINT ["/usr/local/bin/eg-node-entrypoint"]
