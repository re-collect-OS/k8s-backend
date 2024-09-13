# re:collect backend

See also for context: [Overview (backend, frontend, sidecar)](https://github.com/re-collect/k8s-backend/blob/dev/overview.md)

Table of contents

* Local development
    * [Quick start](#local-development)
    * [Using web-client with local backend](#using-web-client-with-local-backend)
    * [Interacting with local mocks of 3rd party APIs](#interacting-with-local-mocks-of-3rd-party-apis)
* Interacting with staging/production
    * [Searching logs in DataDog](#searching-logs-in-datadog)
    * [Exploring DataDog metrics dashboards](#exploring-datadog-metrics-dashboards)
    * [Exploring the kubernetes cluster](#exploring-the-kubernetes-cluster)
    * [Exploring the live DBs](#exploring-the-live-dbs)
* Safely rolling out new features
    * [Using feature flags](#using-feature-flags)
    * [Making changes to the SQL DB](#making-changes-to-the-sql-db)
    * [Making changes to the Vector DB](#making-changes-to-the-vector-db)

## Local development

### Quick start

Visual Studio Code is the recommended IDE for this project. If you don't yet
have it, go ahead and install it:
* On macOS: `brew install --cask visual-studio-code`
* On Ubuntu Desktop: `sudo snap install --classic code`
* Other platforms: https://code.visualstudio.com/download

Once you open VSCode to the root of this project, it'll prompt you to install
the recommended extensions — do so. Once these extensions are installed, you'll
see a prompt to reopen the project in a Dev Container.

> [!NOTE]
> Dev Containers are a great way to ensure that all developers on the team
have the exact same development environment set up, regardless of the platform
they're using (macOS, Linux, Windows). All dependencies are installed in an
isolated environment (container), avoiding conflicts with host system packages.
>
> The determinism and versatility does comes at a small cost in performance.

Dev Containers require an available docker engine so VSCode will prompt you to
download and install Docker Desktop if you don't already have it.

If you prefer to set up your host environment for development instead, run the
setup script and follow on-screen prompts:

```bash
./dev/scripts/setup_local_dev.sh
```

To review development and deployment help (shown after setup above), run:

```bash
poetry info
```

The setup script works on macOS and Ubuntu (Desktop, WSL, or Dev Container).

### Using web-client with local backend

Follow [instructions](https://github.com/re-collect/web-monorepo/blob/dev/web-client/README.md)
to run the web-client locally and open a browser window at http://localhost:3000.

Login with "dev@re-collect.local" and password "recollect" (this user is an admin.)

### Interacting with local mocks of 3rd party APIs

Once `docker-compose up` is called, the following 3rd party APIs are available:

* [Postgres](https://hub.docker.com/_/postgres)
    * **Host:** localhost:5432
    * **Credentials:**
        * User: postgres
        * Password: postgres
        * Database: user_data
* [SQS](https://docs.localstack.cloud/user-guide/aws/sqs/)
    * **Host:** localhost:4566
    * **Credentials:** irrelevant
* [S3](https://docs.localstack.cloud/user-guide/aws/s3/)
    * **Host:** localhost:4566
    * **Credentials:** irrelevant
* [Lambda](https://docs.localstack.cloud/user-guide/aws/lambda/)
    * **Host:** localhost:4566
    * **Credentials:** irrelevant
* [Cognito](https://github.com/jagregory/cognito-local)
    * **Host**: localhost:9229
    * **Credentials**: irrelevant
* [Sendgrid](https://github.com/janjaali/sendGrid-mock)
    * **Host**: localhost:4321
    * **Credentials:**
        * API Key: sendgridapikey

#### Example commands

##### SQS

```bash
# Create a queue
aws --endpoint-url=http://localhost:4566 \
  sqs create-queue \
  --queue-name test-queue

# List queues
aws --endpoint-url=http://localhost:4566 \
  sqs list-queues

# Get queue URL
aws --endpoint-url=http://localhost:4566 \
  sqs get-queue-url \
  --queue-name test-queue

# Send a message to the queue
aws --endpoint-url=http://localhost:4566 \
  sqs send-message \
  --queue-url <queue-url> \
  --message-body "hello world"
```

##### S3

```bash
# Create a bucket
aws --endpoint-url=http://localhost:4566 \
  s3 mb s3://my-test-bucket

# Upload a file
aws --endpoint-url="http://localhost:4566" \
  s3 cp "README.md" s3://my-test-bucket

# Delete a bucket
aws --endpoint-url=http://localhost:4566 \
  s3 rb s3://my-test-bucket
```

##### Lambda

```bash
# Create a and upload simple echo lambda
echo "def echo(e, c): return {'statusCode': 200, 'body': e}" \
  > echo.py
zip echo.zip echo.py
aws --endpoint http://localhost:4566 \
  lambda create-function \
  --function-name echo-py \
  --runtime python3.11 \
  --zip-file fileb://echo.zip \
  --handler echo.echo \
  --role arn:aws:iam::000000000000:role/lambda-role

# Invoke the lambda
aws --endpoint http://localhost:4566 \
  lambda invoke \
  --function-name echo-py \
  --cli-binary-format raw-in-base64-out \
  --payload '{"hello": "world"}' \
  echo.txt

# To update
aws --endpoint http://localhost:4566 \
  lambda update-function-code \
  --function-name echo-py \
  --zip-file fileb://echo.zip
```

##### Cognito

```bash
# List client IDs for pre-configured pool
aws --endpoint http://localhost:9229 \
  cognito-idp list-user-pool-clients \
  --user-pool-id local_pool

# List users in pre-configured pool
aws --endpoint http://localhost:9229 \
  cognito-idp list-users \
  --user-pool-id local_pool \
  --limit 10
```

##### Postgresql

```bash
psql -h localhost -p 5432 -U recollect_local -d user_data
```

##### Sendgrid

UI is available at http://localhost:4321

> [!IMPORTANT]
> (TODO) Add API call examples

## Interacting with staging/production

### Searching logs in DataDog

* Ask a team mate to invite you to re:collect DataDog organization
* Login to DataDog
* On the left menu, click "Logs"
* Use the "Search for" input to narrow down the logs to a specific env or app, e.g.:
    * `env:dev service:external` for all external API staging logs
    * `env:dev service:account_deleter "Deleted all data"` for staging account deletion messages

Be sure to explore and familiarize yourself with this interface, as it'll be
extremely useful for debugging problems.

> [!NOTE]
> It's also possible to stream live logs from either single Pods or Deployments
(i.e. all Pods in a Deployment) using _k9s_ or _click_ but DataDog is a much
friendlier and powerful search interface.

### Exploring DataDog metrics and dashboards

It'd be nice, but we don't have any yet 🙃

### Exploring the kubernetes cluster

Start by configuring AWS CLI credentials and setting the cluster contexts for
production and staging environments:

```bash
aws configure
```

To switch between staging and production contexts, run:

```bash
poetry kubectx dev
poetry kubectx prod
```

To explore the cluster, use [k9s](https://k9scli.io) or [click](https://github.com/databricks/click).

> [!WARNING]
> These tools can be used to perform destructive actions on the cluster.
Exercise caution and consider setting up different color schemes for staging
and prod environments (example [for k9s](https://k9scli.io/topics/skins/)).

### Exploring the live DBs

Open a tunnel to the staging database proxy with:

```bash
poetry dbproxy
```

Then connect to `localhost:5432` with your favorite Postgres client.

For production , use:

```bash
# This is a read-only connection.
poetry dbproxy prod
# Proxy to a different local port.
poetry dbproxy --port 1234 prod
```

## Rolling out new features

### Using feature flags

Refer to the [feature flags](docs/feature_flags.md) documentation.

### Making changes to the SQL DB

> [!IMPORTANT]
> TBD

### Making changes to the Vector DB

> [!IMPORTANT]
> TBD
