#!/bin/bash
set -e

# Use stored Twitter consumer key and secret to obtain a bearer token and update
# the pulumi stack with it.
#
# See: https://developer.twitter.com/en/docs/authentication/oauth-2-0/application-only

case $1 in
  prod) STACK="recollect/prod" ;;
  dev) STACK="recollect/dev" ;;
  *) echo "Invalid env: '$1'; use 'dev' or 'prod'" >&2; exit 1 ;;
esac

# Assumes valid consumer key+secret (aka API key+secret) have been set with:
#   pulumi config set twitter-app-auth-consumer-key --stack <stack> --secret <key>
#   pulumi config set twitter-app-auth-consumer-secret --stack <stack> --secret <secret>
#
# Key+secret cannot be read from Twitter; they must be regenerated if lost.
consumer_key=$(pulumi config get twitter-app-auth-consumer-key --stack $STACK)
consumer_secret=$(pulumi config get twitter-app-auth-consumer-secret --stack $STACK)
credentials=$(echo -n "$consumer_key:$consumer_secret")
b64_credentials=$(echo -n "$credentials" | base64)

response=$(curl -s -X POST \
  -H "Authorization: Basic $b64_credentials" \
  -H "Content-Type: application/x-www-form-urlencoded;charset=UTF-8" \
  -d "grant_type=client_credentials" \
  https://api.twitter.com/oauth2/token)

token_type=$(echo $response | jq -r .token_type)

if [ "$token_type" != "bearer" ]; then
  echo "Error: expecting token_type to be 'bearer' but got '$token_type'" >&2
  echo "Response: $(echo $response | jq)" >&2
  exit 1
fi

bearer_token=$(echo $response | jq -r .access_token)
pulumi config set twitter-app-auth-bearer-token --stack $STACK --secret $bearer_token
echo "Updated $STACK twitter-app-auth-bearer-token secret.

To revoke and generate a new token:
  1. Go to https://developer.twitter.com/en/portal/dashboard
  2. Click the key icon next to the app name
  3. Click the 'Revoke' button
  4. Run this script again (regenerates the token and updates pulumi secret)"
