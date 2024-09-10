#!/bin/bash
set -e

# Select appropriate kubectl context for recollect-backend based on env.
# Sets up new context if not yet defined.

case $1 in
  prod) REGION=us-east-1 ;;
  dev) REGION=us-west-2 ;;
  *) echo "Invalid env: '$1'; use 'dev' or 'prod'" >&2; exit 1 ;;
esac

CLUSTER_NAME=recollect-backend # should be different per env, but alas
ALIAS="recollect-$1"

if ! kubectl config get-contexts -o name | grep -q "^${ALIAS}$"; then
  aws eks update-kubeconfig \
    --region $REGION --name $CLUSTER_NAME --alias $ALIAS
fi
kubectl config use-context $ALIAS
