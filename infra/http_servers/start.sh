#!/usr/bin/env bash

if [ -z "${SERVER_MODULE}" ]; then
  echo "SERVER_MODULE must be set"
  exit 1
fi

# exec replaces current program in current process, which allows OS/docker
# signals to be forwarded to the command below (which must handle them).
exec ddtrace-run uvicorn http_servers.${SERVER_MODULE}:app \
  --proxy-headers --host 0.0.0.0 --port 8080 --workers 4 --no-use-colors
