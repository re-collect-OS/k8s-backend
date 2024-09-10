#!/bin/bash
set -e

# Localstack free version does not have persistence between container restarts.
# Queues, buckets, etc. need to be re-created on every start, hence this being
# a separate script from bootstrap_dependencies.sh (invoked on container start).

# TODO(bruno): local dev setup needs to be agnostic to local aws profile setup.
# Current setup assumes everyone has us-west-2 as their default region (because
# that's the one I had when I set everything up). Perhaps pick a region that
# is neither staging (us-west-2) nor prod (us-east-1)?
export AWS_DEFAULT_REGION="us-west-2"

apt-get install jq -y

buckets=(
  "user-files-local"
)

for bucket in "${buckets[@]}"; do
  echo "* Creating bucket ${bucket}..."
  awslocal s3 mb "s3://$bucket"
done

queues=(
  "account_deletions"
  "embedding_migrations"
  "all_imports"
)

for queue in "${queues[@]}"; do
  echo "* Creating queue $queue with DLQ..."
  # Create DLQ
  dlq_url=$(
    awslocal sqs create-queue --queue-name "${queue}_dlq" \
    | jq -r '.QueueUrl'
  )

  dlq_arn=$(
    awslocal sqs get-queue-attributes \
      --attribute-names QueueArn --queue-url ${dlq_url} | \
    jq -r '.Attributes.QueueArn'
  )

  redrive_policy="{\"maxReceiveCount\":\"10\", \"deadLetterTargetArn\":\"$dlq_arn\"}"
  # Create the main queue with a redrive policy towards DLQ
  awslocal sqs create-queue --queue-name "${queue}" \
    --attributes "VisibilityTimeout=10,RedrivePolicy='$redrive_policy'"
done
