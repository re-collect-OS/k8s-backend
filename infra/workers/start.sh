#!/usr/bin/env bash

if [ -z "${WORKER_MODULE}" ]; then
  echo "WORKER_MODULE must be set"
  exit 1
fi

# exec replaces current program in current process, which allows OS/docker
# signals to be forwarded to the command below (which must handle them).
exec python -u -m workers.${WORKER_MODULE}
