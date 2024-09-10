#!/bin/bash
set -e

# Hit cognito-idp to get a bearer token for the given user.

if [ $# -lt 2 ]; then
  echo "Usage: poetry get-bearer-token [-e <env>] <username> <password>" >&2
  exit 1
fi

username=$1
password=$2
env=${3:-"local"}

case $env in
  local)
    if [ -z "$COGNITO_APP_CLIENT_ID" ]; then
      # This won't happen if the script was run with poetry.
      echo "Error: \$COGNITO_APP_CLIENT_ID is not set" >&2
      exit 1
    fi
    endpoint="http://localhost:9229"
    client_id="$COGNITO_APP_CLIENT_ID" # from .env
    ;;
  dev)
    endpoint="https://cognito-idp.us-west-2.amazonaws.com"
    client_id="37sl5upjevqmrhkhnmr96j8v19" # from Pulumi.dev.yaml
    ;;
  prod)
    endpoint="https://cognito-idp.us-east-1.amazonaws.com"
    client_id="7m3aetoms4q04rd13h93m7lbov" # from Pulumi.prod.yaml
    ;;
  *) echo "Invalid env: '$env'; use 'local', 'dev', or 'prod" >&2; exit 1 ;;
esac

aws cognito-idp initiate-auth \
  --endpoint="$endpoint" \
  --client-id $client_id \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters "USERNAME=$username,PASSWORD=$password" \
  | jq -r '.AuthenticationResult.AccessToken'
