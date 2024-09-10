#!/bin/bash
set -e

# Script that bootstraps databases and 3rd party apps with schemas/data to begin
# local development. This is a relatively poor substitute for being able to
# bootstrap a minikube cluster using pulumi with the same infra setup as we do
# for staging/prod, but it's a decent start.
#
# Safe to re-run.
#
# Pre-reqs:
# - `setup_local_dev.sh` has been successfully run
# - local dev containers are running (docker compose up)
#
# TODO:
# - Lambda setup

# Run from root of project
SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
cd $SCRIPTPATH && cd ../../

pgsql="localhost"
weaviate="http://localhost:8888"
cognito="http://localhost:9229"
s3="http://localhost:4566"
sqs="http://localhost:4566"
psql_url="postgresql://postgres:postgres@${pgsql}:5432/user_data" # pragma: allowlist secret

bootstrap_pgsql() {
  echo -e "\n🐘 Running SQL migrations..."
  # Initial data dump for DB initialization, starting point for migrations.
  # This is required since DB was originally provisioned and changed manually,
  # and migrations are relatively recent (only contain a sub-set of more recent
  # changes). This should be temporary until migrations have been sorted out to
  # a point where they can be used to fully re-create DB.
  psql $psql_url -f ./migrations/pgsql/initial_dump.sql
  alembic upgrade head
}

bootstrap_weaviate() {
  echo -e "\n🍊 Creating Weaviate schema..."
  for json_file in "./migrations/weaviate"/*.json; do
    if [ -f "$json_file" ]; then
      curl -X POST "${weaviate}/v1/schema" \
        -H "Content-Type: application/json" \
        -d @"$json_file" || echo "Failed to create class in $json_file"
    fi
  done
}

cognito_user_email="dev@re-collect.local"
cognito_user_pass="recollect" # pragma: allowlist secret

bootstrap_cognito() {
  # Create user pool and set COGNITO_USER_POOL_ID in .env
  echo -e "\n🔐 Creating Cognito user pool..."
  output=$(aws --endpoint=${cognito} \
    cognito-idp create-user-pool \
      --pool-name "local-dev-pool" \
      --auto-verified-attributes email \
      --username-attributes email \
      --policies "PasswordPolicy={MinimumLength=8,RequireLowercase=false,RequireNumbers=false,RequireSymbols=false,RequireUppercase=false}" \
      --schema "Name=email,Required=true,AttributeDataType=String,Mutable=false" \
      --admin-create-user-config AllowAdminCreateUserOnly=true)
  user_pool_id=$(echo "$output" | jq -r '.UserPool.Id')

  # Create client id in user pool and set COGNITO_CLIENT_ID in .env
  echo -e "\n🔐 Creating Cognito user pool client..."
  output=$(aws --endpoint=${cognito} \
    cognito-idp create-user-pool-client \
      --user-pool-id "$user_pool_id" \
      --client-name "local-dev-client" \
      --no-generate-secret \
      --explicit-auth-flows ADMIN_NO_SRP_AUTH)
  user_pool_client_id=$(echo "$output" | jq -r '.UserPoolClient.ClientId')

  # Create confirmed user and set it as admin in .env
  echo -e "\n🔐 Creating Cognito user..."
  output=$(aws --endpoint=${cognito} \
    cognito-idp admin-create-user \
      --user-pool-id "$user_pool_id" \
      --username "$cognito_user_email" \
      --message-action "SUPPRESS" \
      --user-attributes Name=email,Value=$cognito_user_email Name=custom:name,Value=Developer \
      --temporary-password "temp123") # pragma: allowlist secret
  aws --endpoint=${cognito} \
    cognito-idp admin-set-user-password \
      --user-pool-id "$user_pool_id" \
      --username "$cognito_user_email" \
      --password "$cognito_user_pass" \
      --permanent
  user_id=$(echo $output | jq -r '.User.Attributes[] | select(.Name=="sub") | .Value')
  awk -v user_pool_id="$user_pool_id" \
      -v client_id="$user_pool_client_id" \
      -v user_id="$user_id" \
    '{gsub(/COGNITO_USERPOOL_ID=.*/, "COGNITO_USERPOOL_ID=" user_pool_id); \
      gsub(/COGNITO_APP_CLIENT_ID=.*/, "COGNITO_APP_CLIENT_ID=" client_id); \
      gsub(/AWS_COGNITO_JWKS_PATH=.*/, "AWS_COGNITO_JWKS_PATH=http://localhost:9229/" user_pool_id "/.well-known/jwks.json"); \
      gsub(/ALLOW_ADMIN=.*/, "ALLOW_ADMIN=" user_id)}1' \
    .env > .env.tmp && mv .env.tmp .env


  # Create matching SQL record for this user in DB
  echo -e "\n🐘 Creating SQL user record..."
  psql $psql_url \
    -c "INSERT INTO user_account (user_id, email, name, status, settings, created, modified) \
        VALUES ('$user_id', '$cognito_user_email', 'Developer', 'ready', '{}', now(), now()) \
        ON CONFLICT (email) DO
        UPDATE SET user_id = EXCLUDED.user_id, settings = EXCLUDED.settings, modified = now();"
}

echo "
This script will:

  🐘 Apply all SQL database migrations
  🍊 Create Weaviate schema
  🔐 Bootstrap cognito with:
    - User pool
    - User pool client
    - Confirmed user

⚠️  Before proceeding, make sure that:
  ⚙️  ./dev/scripts/setup_local_dev.sh has been run
  🐳 local dev containers are running (docker compose up)
  🐍 virtual env is activated (source .venv/bin/activate)
"
read -p "Proceed? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Okbai 👋"
    exit 1
fi

bootstrap_pgsql
bootstrap_weaviate
bootstrap_cognito

echo -e "\n🎉 Done! For next steps, see help:

  poetry info

ℹ️  If you need to re-run, first delete all local containers and volumes:

  poetry containers delete
"
