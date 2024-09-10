#!/bin/bash
set -e

# Manage 3rd party API containers (AWS, Postgres, Weaviate, etc.)

COMPOSE_FILE="dev/docker-compose.yaml"
case $1 in
  start)
    # Run all containers in background.
    docker compose -f $COMPOSE_FILE up -d
    ;;
  stop)
    # Stop containers but keep volumes (i.e. keep data/state).
    docker compose -f $COMPOSE_FILE stop
    ;;
  restart)
    docker compose -f $COMPOSE_FILE restart
    ;;
  delete)
    # Stop containers and deletes volumes (requires bootstrapping).
    docker compose -f $COMPOSE_FILE down -v
    ;;
  *)
    echo "Usage: $0 {start|stop|delete}"
    exit 1
esac
