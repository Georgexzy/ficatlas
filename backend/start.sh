#!/bin/sh
# API entry point.
#
# A script rather than an inline compose `command:` because the choice below
# needs a conditional, and expressing that as a folded YAML scalar produced
# "invalid command line string" from compose's argument parser. It is also the
# honest place for it: how the server starts is a property of the server.
#
# WEB_CONCURRENCY is the single knob. uvicorn reads it for the worker count,
# and db/session.py and ratelimit.py both divide by it — the connection-pool
# budget and the rate limits are per deployment, not per process, so all three
# have to agree on one number.
#
# Four workers by default, not twelve. The workers are not the scarce resource:
# search is bound by disk reads against a 39GB database with roughly 2GB of
# cache, so more Python processes buy nothing on the path that actually limits
# the site. They buy a great deal on the cheap paths, which were pinned to one
# core by a single process and one GIL. This box is also somebody's desktop.
#
# --reload is mutually exclusive with --workers. It restarts the API on any file
# change, which also kills anything running inside the container, so it is
# opt-in for working on the code and off for an unattended box.
set -e

if [ "${UVICORN_RELOAD}" = "true" ]; then
    echo "Starting with --reload (single process, development)."
    exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
fi

WORKERS="${WEB_CONCURRENCY:-4}"
echo "Starting with ${WORKERS} worker process(es)."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS}"
