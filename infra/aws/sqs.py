# -*- coding: utf-8 -*-
import json
from dataclasses import dataclass

import pulumi
import pulumi_aws

from infra.common import to_resource_name

from . import iam


@dataclass
class QueuePair:
    """
    A pair of `aws.sqs.Queue` objects — the main queue and its corresponding
    dead-letter queue.
    """

    main_queue: pulumi_aws.sqs.Queue
    deadletter_queue: pulumi_aws.sqs.Queue


def declare_queue_with_dlq(
    queue_name: str,
    aws_provider: pulumi_aws.Provider,
    visibility_timeout_seconds: int = 20,
    message_retention_seconds: int = 7 * 24 * 60 * 60,
    max_delivery_attempts: int = 30,
) -> QueuePair:
    """
    Creates an SQS queue with a dead-letter queue (DLQ) attached.

    Args:
        name (str): The name of the queue to create.
        aws_provider (pulumi_aws.Provider): The AWS provider to use for the
            resources created by this function. Example:
            aws.Provider("us-east-1", region="us-east-1")
        visibility_timeout_seconds (int, optional): The length of time during which the
            queue will be unavailable after a message is received. Defaults to 20.
        message_retention_seconds (int, optional): The length of time that messages will
            be retained in the queue. Defaults to 7 days.
        max_delivery_attempts (int, optional): The maximum number of times that a message
            can be delivered before being sent to the DLQ. Defaults to 30.
    """
    resource_name = to_resource_name(queue_name)
    deadletter_queue = pulumi_aws.sqs.Queue(
        resource_name=f"{resource_name}-dlq",
        name=f"{queue_name}_dlq",
        # always retain DLQ messages for 14 days (SQS max allowed retention)
        message_retention_seconds=14 * 24 * 60 * 60,
        opts=pulumi.ResourceOptions(
            # Just so queues don't appear at the top-level of the stack.
            parent=aws_provider,
            provider=aws_provider,
        ),
    )
    deadletter_queue_arn: pulumi.Output[str] = deadletter_queue.arn
    main_queue = pulumi_aws.sqs.Queue(
        resource_name=resource_name,
        name=queue_name,
        visibility_timeout_seconds=visibility_timeout_seconds,
        message_retention_seconds=message_retention_seconds,
        redrive_policy=deadletter_queue_arn.apply(
            lambda dlq_arn: json.dumps(
                {
                    "deadLetterTargetArn": dlq_arn,
                    "maxReceiveCount": max_delivery_attempts,
                },
            ),
        ),
        opts=pulumi.ResourceOptions(
            parent=aws_provider,
            provider=aws_provider,
            depends_on=[deadletter_queue],
        ),
    )

    # To support multi-region setups, accept a list of aws_provider and
    # return a list of QueuePair (one per region).
    return QueuePair(main_queue, deadletter_queue)


def consume_from_queues(
    *queue: pulumi_aws.sqs.Queue,
) -> pulumi.Output[iam.PolicyStatement]:
    """
    Returns a `pulumi.Output` that'll materialize into a `PolicyStatement` entry
    that specifies the permissions to perform the minimal set of operations
    required to consume messages from the list of supplied `aws.sqs.Queue`.
    """
    if not queue:
        raise ValueError("Must supply at least one queue to consume from.")

    queue_arns: list[pulumi.Output[str]] = [q.arn for q in queue]
    return pulumi.Output.all(*queue_arns).apply(
        lambda arns: {
            "Effect": "Allow",
            "Action": [
                "sqs:GetQueueUrl",
                "sqs:ReceiveMessage",
                "sqs:SendMessage",
                "sqs:DeleteMessage",
                "sqs:ChangeMessageVisibility",  # to delay retries
            ],
            "Resource": arns,
        },
    )


def publish_to_queues(
    *queue: pulumi_aws.sqs.Queue,
) -> pulumi.Output[iam.PolicyStatement]:
    """
    Returns a `pulumi.Output` that'll materialize into a `PolicyStatement` entry
    that specifies the permissions to perform the minimal set of operations
    required to publish messages to the list of supplied `aws.sqs.Queue`.
    """
    if not queue:
        raise ValueError("Must supply at least one queue to publish to.")

    queue_arns: list[pulumi.Output[str]] = [q.arn for q in queue]
    return pulumi.Output.all(*queue_arns).apply(
        lambda arns: {
            "Effect": "Allow",
            "Action": [
                "sqs:GetQueueUrl",
                "sqs:SendMessage",
            ],
            "Resource": arns,
        },
    )
