#!/bin/bash
set -e

# Setup a port-forward to the dev or prod database on local port 5432.
#
# To connect to the database, run:
#   psql -h localhost -p 5432 -U postgres -d postgres

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

env=${1:-"dev"}
local_port=${2:-"5432"}
connect=${3:-""}

case $env in
  dev)
    context="recollect-dev"
    stack="recollect/dev"
    color=$GREEN
    ;;
  prod)
    echo -e "\n⚠️ $RED You are connecting to the prod database!${NC} ⚠️\n"
    context="recollect-prod"
    stack="recollect/prod"
    color=$RED
    ;;
  *) echo "Invalid env: '$env'; use 'dev' or 'prod'" >&2; exit 1 ;;
esac

cenv="${color}${env}${NC}"
app="dbproxy"

# Pick the first pod from the dbproxy deployment in the tools namespace.
pods=$(kubectl --context $context get pods -l app=$app -n tools -o name)
if [ -z "$pods" ]; then
    echo -e "[$cenv] Error: No dbproxy pods running."
    exit 1
fi
first_pod=$(echo "$pods" | head -n 1)
# Strip pod/ prefix
first_pod_name=${first_pod#pod/}

echo -e "[$cenv] Setting up port-forwarding to $first_pod_name:5432..."
portfwd_cmd="kubectl --context $context --namespace tools port-forward $first_pod_name $local_port:5432"

# If not connecting, run the port-forwarding command and exit.
if [ "$connect" != "connect" ]; then
  $portfwd_cmd
  echo -e "[$cenv] Port-forwarding to $first_pod_name:$local_port terminated."
  exit 0
fi

# Otherwise, run it in the background and launch authenticated psql.
# First, check that we're logged in to pulumi (required to load secrets to
# establish psql connection)
if ! pulumi whoami > /dev/null 2>&1; then
  echo "! Must be logged in to pulumi to retrieve database connection secrets (run 'pulumi login')" >&2
  exit 1
fi

# NB(bruno): Using screen is the only method I could find to run the port
# forward process in the background without getting SIGINT whenever this script
# gets ctrl+c'd.
session_name="${env}-dbproxy-portfwd"
screen -dmS $session_name $portfwd_cmd
sleep 1

# Get pid of kubectl port-forward command running in screen
screen_pid=$(screen -ls | grep "$session_name" | awk -F '.' '{print $1}' | awk '{print $1}')
fwd_pid=$(pgrep -P $screen_pid)

echo -e "[$cenv] Connecting to $first_pod_name:$local_port..."
pg_user=$(pulumi config get --stack $stack postgresql-user)
pg_pass=$(pulumi config get --stack $stack postgresql-password)

PGPASSWORD="$pg_pass" psql -h localhost -p $local_port -U $pg_user -d user_data

# Once psql exists, kill the forward process. Required on macos, as quitting
# the screen session won't kill the process.
kill $fwd_pid
echo -e "[$cenv] Port-forwarding to $first_pod_name:$local_port terminated."
