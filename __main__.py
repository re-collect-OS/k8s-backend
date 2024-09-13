# -*- coding: utf-8 -*-
import base64
import json
import os

import pulumi
import pulumi_aws as aws
import pulumi_eks as eks
import pulumi_kubernetes as k8s
from pulumi_docker import BuilderVersion, DockerBuildArgs, Image
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs

from infra.app import declare_app_zone
from infra.aws.cognito import delete_users_in_pool
from infra.aws.ecr import declare_image_in_ecr
from infra.aws.loadbalancer import declare_alb_controller
from infra.aws.s3 import (
    delete_objects_in_bucket,
    get_objects_in_bucket,
    list_objects_in_bucket,
    put_objects_in_bucket,
)
from infra.aws.sqs import consume_from_queues, declare_queue_with_dlq, publish_to_queues
from infra.common import base64_str, image_sha, to_k8s_secret
from infra.datadog.agent import (
    datadog_annotations,
    datadog_labels,
    declare_datadog_cluster_agent,
)
from infra.dbproxy import declare_dbproxy
from infra.http_servers.http_server import PublicHttpServer, declare_http_server
from infra.workers.worker import declare_worker

name = "recollect"
cluster_name = f"{name}-backend"
cluster_tag = f"kubernetes.io/cluster/{cluster_name}"

stack = pulumi.get_stack()
aws_config = pulumi.Config("aws")
# Get some values from the Pulumi configuration (or use defaults)
config = pulumi.Config()

env = stack  # dev or prod, from infra/Pulumi.<stack>.yaml
certificate_arn = config.get("certificate-arn", None)
k8s_namespace = config.get("namespace", "default")
cluster_node_instance_type = config.get("eks-cluster-node-instance-type") or "t3.medium"
cluster_desired_nodes = config.get_int("eks-cluster-desired-nodes") or 3
cluster_min_nodes = config.get_int("eks-cluster-min-nodes") or 3
cluster_max_nodes = config.get_int("eks-cluster-max-nodes") or 6
weaviate_instance_type = config.get("weaviate-instance-type") or "r6a.2xlarge"
gpu_node_instance_type = config.get("gpu-node-instance-type") or "g4dn.xlarge"
llm_node_instance_type = config.get("llm-gpu-node-instance-type") or "g4dn.xlarge"
ray_head_node_instance_type = config.get("ray-head-instance-type") or "m5.xlarge"
vpc_cidr = config.require("vpc-network-cidr")


def run_checks():
    is_ci = os.getenv("GITHUB_ACTION") or False
    # Only run checks for production
    if stack == "prod" and not is_ci:
        print(
            """
⛔️ Production deployments only from CI ⛔️
   -----------------------------------
If this is a justifiable exception:
- Notify the team (#engineering on Slack)
- Set GITHUB_ACTION=true and re-run your command
"""
        )
        exit(1)


run_checks()

# Create a VPC for the EKS cluster
#
# we need to tag the public subnets for the AWS Load Balancer Controller:
# kubernetes.io/cluster/${cluster-name} owned
# kubernetes.io/role/elb 1
#
# https://aws.amazon.com/premiumsupport/knowledge-center/eks-load-balancer-controller-subnets/
# https://github.com/pulumi/pulumi-eks/tree/master/examples/subnet-tags
#
# not true: to tag subnets, we have to use aws.ec2.Vpc - not awsx.ec2.Vpc
# but there actually was an issue: https://github.com/pulumi/pulumi-awsx/pull/974
# https://pulumi.awsworkshop.io/50_eks_platform/20_provision_cluster/20_create_cluster.html
# - but how do we get default route table id?
#
# https://www.learnaws.org/2021/06/22/aws-eks-alb-controller-pulumi/

availability_zones = config.get_object("availability-zones")
public_subnet_cidrs = config.get_object("public-subnet-cidrs")
# private subnet can access the internet by using a network address translation
# (NAT) gateway that resides in the public subnet, those need Elastic IP configured
private_subnet_cidrs = config.get_object("private-subnet-cidrs")

# sanity check
if env == "prod":
    assert "us-east-1a" in availability_zones
else:
    assert "us-west-2a" in availability_zones

# ??? awsx.ec2.Vpc does not have vpc.default_security_group_id available?
vpc = aws.ec2.Vpc(
    f"eks-vpc-{name}",
    cidr_block=vpc_cidr,
    enable_dns_support=True,
    enable_dns_hostnames=True,
    tags={
        "Name": f"eks-vpc-{name}",
    },
)

igw = aws.ec2.InternetGateway(
    f"eks-igw-{name}",
    vpc_id=vpc.id,
    tags={
        "Name": f"eks-igw-{name}",
    },
    opts=pulumi.ResourceOptions(parent=vpc),
)

# https://blog.scottlowe.org/2021/08/26/establishing-vpc-peering-with-pulumi-and-go/
# * to establish a VPC peering relationship, a few different resources are needed
#   (note that each of these is considered its own independent Pulumi resource,
#   not a property of another resource):
#
#    * The VPC peering connection, which references the VPC IDs on both sides
#    * The VPC peering connector accepter, which references the VPC peering connection
#    * New routes to direct traffic between the two VPC CIDRs (these wouldn’t already exist
#      because these routes need to reference the VPC peering connection in order to direct traffic appropriately)
#    * New security group rules to allow traffic from the peer VPC CIDR (unless this traffic is already allowed)

# attach VPC peering ingress/egress rules to VPC default security group
aws.ec2.SecurityGroupRule(
    f"peer-src-sec-grp-rule-ingress",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    cidr_blocks=[config.require("aurora-vpc-network-cidr")],
    security_group_id=vpc.default_security_group_id,
    description="PostgreSQL in from RDS",
)

aws.ec2.SecurityGroupRule(
    f"peer-src-sec-grp-rule-egress",
    type="egress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    cidr_blocks=[config.require("aurora-vpc-network-cidr")],
    security_group_id=vpc.default_security_group_id,
    description="PostgreSQL out to RDS",
)

# peer VPCs k8s <-> Aurora managed PostgreSQL

# different regions -> separate connection and accepter
vpc_peering_connection = aws.ec2.VpcPeeringConnection(
    f"vpc-peering-connection",
    vpc_id=vpc.id,
    peer_vpc_id=config.require("aurora-rds-vpc-id"),
    peer_owner_id=config.require("aurora-rds-owner-id"),
    peer_region=config.require("aurora-rds-region"),
    auto_accept=False,
    # knwon issue, will fail first: https://github.com/pulumi/pulumi-aws/issues/2248
    # workaround, comment out the requester block then uncomment and re-apply:
    requester=aws.ec2.VpcPeeringConnectionRequesterArgs(
        allow_remote_vpc_dns_resolution=True,
    ),
    tags={
        "side": "requester",
    },
    opts=pulumi.ResourceOptions(
        depends_on=[vpc],
    ),
)

provider_peer = aws.Provider(
    f"aurora-rds-region",
    region=config.require("aurora-rds-region"),
)

vpc_peering_connection_accepter = aws.ec2.VpcPeeringConnectionAccepter(
    f"vpc-peering-connection-accepter",
    vpc_peering_connection_id=vpc_peering_connection.id,
    auto_accept=True,
    accepter=aws.ec2.VpcPeeringConnectionAccepterArgs(
        allow_remote_vpc_dns_resolution=True,
    ),
    tags={
        "side": "accepter",
    },
    opts=pulumi.ResourceOptions(provider=provider_peer),
)

public_subnet_ids = []
nat_gateway_ids = []

# create public subnets that will be used for the AWS Load Balancer Controller
for n, elem in enumerate(zip(availability_zones, public_subnet_cidrs)):
    zone, public_subnet_cidr = elem

    public_subnet = aws.ec2.Subnet(
        f"eks-public-subnet-{zone}",
        assign_ipv6_address_on_creation=False,
        vpc_id=vpc.id,
        map_public_ip_on_launch=True,
        cidr_block=public_subnet_cidr,
        availability_zone=zone,
        tags={
            # subnet tags for load balancer
            "Name": f"eks-public-subnet-{zone}",
            cluster_tag: "owned",
            "kubernetes.io/role/elb": "1",
        },
        opts=pulumi.ResourceOptions(parent=vpc),
    )

    eip = aws.ec2.Eip(
        f"eks-eip-{n}",
        vpc=True,
        tags={
            "Name": f"eks-eip-{n}",
        },
        opts=pulumi.ResourceOptions(parent=public_subnet),
    )

    nat_gateway = aws.ec2.NatGateway(
        f"eks-private-subnet-ngw-{zone}",
        allocation_id=eip.allocation_id,
        connectivity_type="public",
        subnet_id=public_subnet.id,
        tags={
            "Name": f"eks-ngw-{zone}",
        },
        opts=pulumi.ResourceOptions(parent=public_subnet),
    )

    public_route_table = aws.ec2.RouteTable(
        f"eks-public-route-table-{zone}",
        vpc_id=vpc.id,
        routes=[
            {
                "cidr_block": config.require("aurora-vpc-network-cidr"),
                "vpc_peering_connection_id": vpc_peering_connection.id,
            },
            {"cidr_block": "0.0.0.0/0", "gateway_id": igw.id},
        ],
        tags={
            "Name": f"eks-public-route-table-{zone}",
        },
        opts=pulumi.ResourceOptions(parent=public_subnet),
    )

    aws.ec2.RouteTableAssociation(
        f"eks-public-rta-{zone}",
        route_table_id=public_route_table.id,
        subnet_id=public_subnet.id,
        opts=pulumi.ResourceOptions(parent=public_route_table),
    )

    nat_gateway_ids.append(nat_gateway.id)
    public_subnet_ids.append(public_subnet.id)


private_subnet_ids = []
# create private subnets that have routes to the VPC peering connection
for n, elem in enumerate(zip(availability_zones, private_subnet_cidrs)):
    zone, private_subnet_cidr = elem
    private_subnet = aws.ec2.Subnet(
        f"eks-private-subnet-{zone}",
        assign_ipv6_address_on_creation=False,
        vpc_id=vpc.id,
        cidr_block=private_subnet_cidr,
        private_dns_hostname_type_on_launch="ip-name",
        availability_zone=zone,
        tags={
            # Custom tags for subnets
            "Name": f"eks-private-subnet-{zone}",
            cluster_tag: "shared",
            "kubernetes.io/role/internal-elb": "1",
        },
        opts=pulumi.ResourceOptions(parent=vpc),
    )

    # https://docs.aws.amazon.com/eks/latest/userguide/network_reqs.html
    # "The instances in the public subnet can send outbound traffic directly to the internet,
    # whereas the instances in the private subnet can't.
    # Instead, the instances in the private subnet can access the internet by using a
    # network address translation (NAT) gateway that resides in the public subnet."
    # !! needed for AWS services nodes and pods need to communicate with (ELB, S3, etc.)

    private_route_table = aws.ec2.RouteTable(
        f"eks-private-route-table-{zone}",
        vpc_id=vpc.id,
        routes=[
            {
                "cidr_block": config.require("aurora-vpc-network-cidr"),
                "vpc_peering_connection_id": vpc_peering_connection.id,
            },
            {
                "cidr_block": "0.0.0.0/0",
                "nat_gateway_id": nat_gateway_ids[n],
            },
        ],
        tags={
            "Name": f"eks-private-route-table-{zone}",
        },
        opts=pulumi.ResourceOptions(parent=private_subnet),
    )

    aws.ec2.RouteTableAssociation(
        f"eks-private-rta-{zone}",
        route_table_id=private_route_table.id,
        subnet_id=private_subnet.id,
        opts=pulumi.ResourceOptions(parent=private_route_table),
    )

    private_subnet_ids.append(private_subnet.id)


# routing RDS to k8s
aws.ec2.Route(
    "peer-dst-route",
    route_table_id=config.require("aurora-routetable-id"),
    destination_cidr_block=vpc_cidr,
    vpc_peering_connection_id=vpc_peering_connection.id,
    opts=pulumi.ResourceOptions(provider=provider_peer),
)

# aurora RDS has security group sg-0e838a95206647b3b
# attach ingress/egress security group rules
aws.ec2.SecurityGroupRule(
    f"peer-dst-sec-grp-rule-ingress",
    type="ingress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    cidr_blocks=[vpc_cidr],
    security_group_id=config.require("aurora-securitygroup-id"),
    description="PostgreSQL in from k8s backend",
    opts=pulumi.ResourceOptions(provider=provider_peer),
)

aws.ec2.SecurityGroupRule(
    f"peer-dst-sec-grp-rule-egress",
    type="egress",
    from_port=5432,
    to_port=5432,
    protocol="tcp",
    cidr_blocks=[vpc_cidr],
    security_group_id=config.require("aurora-securitygroup-id"),
    description="PostgreSQL out to k8s backend",
    opts=pulumi.ResourceOptions(provider=provider_peer),
)


# Creates a role and attaches the EKS worker node IAM managed policies
def create_role(name: str) -> aws.iam.Role:
    managed_policy_arns = [
        "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    ]

    role = aws.iam.Role(
        name,
        assume_role_policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AllowAssumeRole",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "ec2.amazonaws.com",
                        },
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )

    for i, policy in enumerate(managed_policy_arns):
        aws.iam.RolePolicyAttachment(
            f"{name}_{managed_policy_arns[i].split('/')[-1]}",
            policy_arn=policy,
            role=role.id,
        )

    return role


compute_role = create_role("eks-compute-role")

# set up cluster encrytion for Secrets
cluster_key = aws.kms.Key(
    "cluster-encryption-key",
    deletion_window_in_days=10,
    description="cluster-encryption-key",
    enable_key_rotation=True,
    tags={
        "Name": f"cluster-encryption-key-{name}",
    },
)

# Create the EKS cluster
# TODO - we'll want to introduce a cluster admin role and move away from the direct user mapping
cluster = eks.Cluster(
    f"cluster-{name}",
    name=cluster_name,
    # Put the cluster in the new VPC created earlier
    vpc_id=vpc.id,
    create_oidc_provider=True,
    gpu=True,
    # Public subnets will be used for load balancers
    public_subnet_ids=public_subnet_ids,
    # Private subnets will be used for cluster nodes
    private_subnet_ids=private_subnet_ids,
    # Change configuration values to change any of the following settings
    instance_type=cluster_node_instance_type,
    desired_capacity=cluster_desired_nodes,
    min_size=cluster_min_nodes,
    max_size=cluster_max_nodes,
    # Do not give worker nodes a public IP address
    node_associate_public_ip_address=False,
    # Uncomment the next two lines for private cluster (VPN access required)
    # endpoint_private_access=true,
    # endpoint_public_access=false
    skip_default_node_group=True,
    # encryption needs setup, ARN of the Key Management Service (KMS) customer master key
    # https://www.pulumi.com/registry/packages/aws/api-docs/eks/cluster/
    encryption_config_key_arn=cluster_key.arn.apply(lambda arn: arn),
    instance_roles=[compute_role],
    user_mappings=list(
        map(
            lambda arn: {"groups": ["system:masters"], "user_arn": arn},
            [
                "arn:aws:iam::338164343182:user/andre_prod",
                "arn:aws:iam::338164343182:user/alicealbrecht_prod",
                "arn:aws:iam::338164343182:user/mihai_cernusca_prod",
                "arn:aws:iam::338164343182:user/cicd",
                "arn:aws:iam::338164343182:user/jonathan_diaz_prod",
                "arn:aws:iam::338164343182:user/bruno",
            ],
        )
    ),
)

cluster_provider = k8s.Provider(
    f"eks-provider",
    enable_server_side_apply=True,
    kubeconfig=cluster.kubeconfig_json,
)

# provision EBS driver addon for weaviate StatefulSet
# manual EBS configuration reference:
# https://stackoverflow.com/questions/73871493/error-while-installing-mongodb-in-aws-eks-cluster-running-prebind-plugin-volu
cluster_oidc_url = cluster.core.oidc_provider.url
cluster_oidc_arn = cluster.core.oidc_provider.arn

ebs_service_account_name = "system:serviceaccount:kube-system:ebs-csi-controller-sa"

ebs_role = aws.iam.Role(
    f"ebs-csi-driver-role",
    assume_role_policy=pulumi.Output.all(cluster_oidc_arn, cluster_oidc_url).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Federated": args[0],
                        },
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                f"{args[1]}:sub": ebs_service_account_name,
                                f"{args[1]}:aud": "sts.amazonaws.com",
                            }
                        },
                    }
                ],
            }
        )
    ),
)

ebs_driver_policy = aws.iam.RolePolicyAttachment(
    f"ebs-csi-driver-role_AmazonEBSCSIDriverPolicy",
    role=ebs_role.id,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
    opts=pulumi.ResourceOptions(
        parent=ebs_role,
    ),
)

aws_ebs_csi_driver = aws.eks.Addon(
    f"ebs-csi-driver",
    cluster_name=cluster.name,
    addon_name="aws-ebs-csi-driver",
    addon_version="v1.15.0-eksbuild.1",
    service_account_role_arn=ebs_role.arn,
    # resolve_conflicts="PRESERVE", # only for upgrade, not creation
    opts=pulumi.ResourceOptions(
        depends_on=[cluster, ebs_role],
    ),
)

# https://github.com/pulumi/pulumi-eks/blob/master/examples/managed-nodegroups-py/__main__.py
#
# If you are running a stateful application across multiple Availability Zones that
# is backed by Amazon EBS volumes and using the Kubernetes Cluster Autoscaler,
# you should configure multiple node groups, each scoped to a single Availability Zone.
# In addition, you should enable the --balance-similar-node-groups feature.
managed_node_groups = []
for n, elem in enumerate(zip(availability_zones, public_subnet_ids)):
    zone, public_subnet_id = elem

    group = eks.ManagedNodeGroup(
        f"compute-managed-ng-{zone}",
        cluster=cluster,
        ami_type="AL2_x86_64",
        node_group_name=f"compute-managed-ng-{zone}",
        node_role_arn=compute_role.arn,
        scaling_config=aws.eks.NodeGroupScalingConfigArgs(
            desired_size=cluster_desired_nodes,
            min_size=cluster_min_nodes,
            max_size=cluster_max_nodes,
        ),
        disk_size=80,
        instance_types=[cluster_node_instance_type],
        labels={"ondemand": "true"},
        subnet_ids=[public_subnet_id],
        tags={"org": "pulumi"},
        opts=pulumi.ResourceOptions(parent=cluster),
    )
    managed_node_groups.append(group)

gpu_managed_node_groups = []
for n, elem in enumerate(zip(availability_zones, public_subnet_ids)):
    zone, public_subnet_id = elem

    if n == 0:
        az_desired_size = 1
        az_min_size = 1
        az_max_size = 1
    else:
        az_desired_size = 0
        az_min_size = 0
        az_max_size = 1

    gpu_compute_managed_ng = eks.ManagedNodeGroup(
        f"gpu-compute-managed-ng-{zone}",
        cluster=cluster,
        ami_type="AL2_x86_64_GPU",
        node_group_name=f"gpu-compute-managed-ng-{zone}",
        node_role_arn=compute_role.arn,
        scaling_config=aws.eks.NodeGroupScalingConfigArgs(
            desired_size=az_desired_size,
            min_size=az_min_size,
            max_size=az_max_size,
        ),
        disk_size=120,
        # capacity_type="SPOT",
        instance_types=[gpu_node_instance_type],
        # labels={"ondemand": "false"},
        labels={"ondemand": "true", "gpu": "true"},
        tags={"org": "pulumi"},
        # taints=[
        #    aws.eks.NodeGroupTaintArgs(
        #        effect="NO_SCHEDULE", key="dedicated", value="gpu-enabled"
        #    )
        # ],
        opts=pulumi.ResourceOptions(parent=cluster),
    )
    gpu_managed_node_groups.append(group)

# provision NVIDIA GPU operator for weaviate and ray
nvidia_ns = k8s.core.v1.Namespace(
    "nvidia-gpu-operator",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="nvidia-gpu-operator",
    ),
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
    ),
)

gpu_operator = Release(
    "nvidia-gpu-operator-op",
    repository_opts=RepositoryOptsArgs(
        repo="https://nvidia.github.io/gpu-operator",
    ),
    chart="gpu-operator",
    version="v22.9.2",
    values={
        "version": "v22.9",
        # NOTE: if updating the toolkit version, it is advised that you create a fresh node group a new operator to go with it
        # Otherwise, you run the risk of installing over the previous version, and things may be inconsistent
        "toolkit": {
            "version": "v1.12.0-centos7",
        },
        # https://github.com/NVIDIA/gpu-operator/issues/270
        # https://github.com/NVIDIA/gpu-operator/blob/master/deployments/gpu-operator/values.yaml#L14
        "daemonsets": {
            "tolerations": [
                {
                    "key": "dedicated",
                    "value": "gpu-enabled",
                    "effect": "NoSchedule",
                },
                {
                    "key": "nvidia.com/gpu",
                    "effect": "NoSchedule",
                },
            ],
        },
    },
    namespace=nvidia_ns,
    opts=pulumi.ResourceOptions(
        depends_on=[managed_node_groups[0], gpu_managed_node_groups[0]],
        provider=cluster_provider,
        # Ignore changes to checksum; bug in pulumi-kubernetes; see:
        # https://github.com/pulumi/pulumi-kubernetes/issues/2649
        # Remove this once that issue is resolved.
        ignore_changes=["checksum"],
    ),
)

ray_managed_ng = eks.ManagedNodeGroup(
    f"ray-managed-ng",
    cluster=cluster,
    subnet_ids=[public_subnet_ids[0]],  # = az us-****-1*; TODO revisit
    ami_type="AL2_x86_64",
    node_group_name=f"ray-managed-ng",
    node_role_arn=compute_role.arn,
    scaling_config=aws.eks.NodeGroupScalingConfigArgs(
        desired_size=1,
        min_size=1,
        max_size=1,
    ),
    disk_size=240,
    instance_types=[ray_head_node_instance_type],
    labels={"ondemand": "true", "ray": "true"},
    tags={"org": "pulumi"},
    taints=[
        aws.eks.NodeGroupTaintArgs(effect="NO_SCHEDULE", key="dedicated", value="ray")
    ],
    opts=pulumi.ResourceOptions(parent=cluster),
)


# weaviate
#
# vector database to create embeddings and semantic search

# weaviate desired memory capacity estimate, assuming all user_ids are moved:
# ~ 22 million sentences = 22e6 × 768 × 4 × 2 ÷ 1e9 =~ 138 GB
# selector: app=weaviate
# important: both the weaviate pod with persistent volume claim and the persistent
# volume the data is in have to be in the same availability zone
weaviate_import_managed_ng = eks.ManagedNodeGroup(
    f"weaviate-import-managed-ng",
    cluster=cluster,
    subnet_ids=[public_subnet_ids[1]],  # = az us-west-2b; TODO revisit
    ami_type="AL2_x86_64",
    node_group_name=f"weaviate-import-managed-ng",
    node_role_arn=compute_role.arn,
    scaling_config=aws.eks.NodeGroupScalingConfigArgs(
        desired_size=1,
        min_size=1,
        max_size=1,
    ),
    disk_size=500,
    instance_types=[weaviate_instance_type],
    labels={"ondemand": "true"},
    tags={"org": "pulumi"},
    taints=[
        aws.eks.NodeGroupTaintArgs(
            effect="NO_SCHEDULE", key="dedicated", value="weaviate-import"
        )
    ],
    opts=pulumi.ResourceOptions(parent=cluster),
)

ns = "weaviate"
weaviate_ns = k8s.core.v1.Namespace(
    f"{ns}",
    metadata={
        "name": ns,
        "labels": {
            "app.kubernetes.io/name": f"{ns}",
        },
    },
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
    ),
)

# S3 bucket for Weaviate backups
weaviate_bucket = aws.s3.Bucket(
    "weaviate-backups",
    acl="private",
)

# name of k8s Service Account to allow Weaviate to speak with the backup bucket
weaviate_service_account_name = f"system:serviceaccount:{ns}:weaviate-serviceaccount"
weaviate_role = aws.iam.Role(
    f"weaviate-role",
    assume_role_policy=pulumi.Output.all(cluster_oidc_arn, cluster_oidc_url).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Federated": args[0],
                        },
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                f"{args[1]}:sub": weaviate_service_account_name,
                                f"{args[1]}:aud": "sts.amazonaws.com",
                            }
                        },
                    }
                ],
            }
        )
    ),
)


# Allow access to be able to list bucket contents and to interact with those objects
def generate_weaviate_backup_policy_doc(arn):
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"{arn}"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:*Object"],
                    "Resource": [f"{arn}/*"],
                },
            ],
        }
    )


weaviate_backup_policy = aws.iam.RolePolicy(
    f"weaviate-backup-policy",
    role=weaviate_role,
    policy=weaviate_bucket.arn.apply(
        lambda arn: generate_weaviate_backup_policy_doc(arn)
    ),
    opts=pulumi.ResourceOptions(
        parent=weaviate_role,
    ),
)

weaviate_service_account = k8s.core.v1.ServiceAccount(
    "weaviate-serviceaccount",
    metadata={
        "name": "weaviate-serviceaccount",
        "namespace": ns,
        "annotations": {
            "eks.amazonaws.com/role-arn": weaviate_role.arn.apply(lambda arn: arn)
        },
    },
)

weaviate_transformers_enabled = False
weaviate_transformers_replicas = 0

# ddog_annotations = {
#    "ad.datadoghq.com/weaviate.checks": {
#        "weaviate": {
#            "init_config": {},
#            "instances": [
#                {
#                    "openmetrics_endpoint": "http://weaviate-0.weaviate.svc.cluster.local:2112/metrics",
#                    "weaviate_api_endpoint": "http://weaviate-0.weaviate.svc.cluster.local:8080",
#                },
#            ],
#        },
#    },
# }
#
#
## Helper function to add custom annotations to Service objects
# def add_custom_annotations(obj, annotations):
#    # Check if the object is a Service and has annotations
#    if (
#        obj["kind"] == "Service"
#        and "metadata" in obj
#        and "annotations" in obj["metadata"]
#    ):
#        # Merge the custom annotations with the existing ones
#        obj["metadata"]["annotations"] = {
#            **obj["metadata"].get("annotations", {}),
#            **annotations,
#        }


# S3 backup bucket is specified in ./weaviate/values.yaml
weaviate = Release(
    "weaviate",
    ReleaseArgs(
        chart="weaviate",  # change to remote repo+values dict
        namespace=weaviate_ns,
        repository_opts=RepositoryOptsArgs(
            repo="https://weaviate.github.io/weaviate-helm",
        ),
        version="16.1.0",
        values={
            "image": {
                "registry": "docker.io",
                "tag": "1.22.7",
                "repo": "semitechnologies/weaviate",
            },
            "env": {
                "CLUSTER_GOSSIP_BIND_PORT": 7000,
                "CLUSTER_DATA_BIND_PORT": 7001,
                # The aggressiveness of the Go Garbage Collector. 100 is the default value.
                "GOGC": 100,
                # Expose metrics on port 2112 for Prometheus to scrape
                "PROMETHEUS_MONITORING_ENABLED": True,
                # Set a MEM limit for the Weaviate Pod so it can help you both increase GC-related
                # performance as well as avoid GC-related out-of-memory (“OOM”) situations
                # GOMEMLIMIT: 6GiB
                # Maximum results Weaviate can query with/without pagination
                # NOTE: Affects performance, do NOT set to a very high value.
                # The default is 100K
                "QUERY_MAXIMUM_RESULTS": 100000,
                # whether to enable vector dimensions tracking metric
                "TRACK_VECTOR_DIMENSIONS": False,
            },
            "tolerations": [
                {
                    "key": "dedicated",
                    "value": "weaviate-import",
                    "effect": "NoSchedule",
                },
            ],
            "nodeSelector": {
                "eks.amazonaws.com/nodegroup": "weaviate-import-managed-ng",
            },
            "backups": {
                "s3": {
                    "enabled": True,
                    "envconfig": {
                        "BACKUP_S3_BUCKET": weaviate_bucket.id.apply(lambda arn: arn),
                    },
                    "serviceAccountName": "weaviate-serviceaccount",
                },
            },
            "modules": {
                "text2vec-contextionary": {
                    "enabled": True,
                },
                "text2vec-transformers": {
                    "enabled": weaviate_transformers_enabled,
                    "tag": "sentence-transformers-all-mpnet-base-v2",
                    "repo": "semitechnologies/transformers-inference",
                    "registry": "docker.io",
                    "replicas": weaviate_transformers_replicas,
                    "envconfig": {
                        "enable_cuda": True,
                    },
                    "nodeSelector": {
                        "beta.kubernetes.io/instance-type": gpu_node_instance_type,
                    },
                    "tolerations": [
                        {
                            "key": "dedicated",
                            "value": "gpu-enabled",
                            "effect": "NoSchedule",
                        },
                    ],
                },
            },
        },
    ),
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
        # transformations=[lambda obj: add_custom_annotations(obj, ddog_annotations)],
        depends_on=[
            aws_ebs_csi_driver,
            managed_node_groups[0],
            gpu_managed_node_groups[0],
            weaviate_import_managed_ng,
            weaviate_service_account,
        ],
        # Ignore changes to checksum; bug in pulumi-kubernetes; see:
        # https://github.com/pulumi/pulumi-kubernetes/issues/2649
        # Remove this once that issue is resolved.
        ignore_changes=["checksum"],
    ),
)

# kuberay
#
# manages a ray cluster on kubernetes, this is where ML models live
#
# https://docs.ray.io/en/latest/cluster/kubernetes/index.html
ns = "ray"
ray_ns = k8s.core.v1.Namespace(
    f"{ns}",
    metadata={
        "name": ns,
        "labels": {
            "app.kubernetes.io/name": f"{ns}",
        },
    },
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
    ),
)

# NOTE: if upgrading, it might be necessary to delete the CRDs like so:
# andre@titan:~$ kubectl get crd --context dev | grep ray
# rayclusters.ray.io                           2023-03-03T09:20:37Z
# rayjobs.ray.io                               2023-03-03T09:20:39Z
# rayservices.ray.io                           2023-03-03T09:20:41Z
# andre@titan:~$ kubectl delete crd rayclusters.ray.io --context dev
# customresourcedefinition.apiextensions.k8s.io "rayclusters.ray.io" deleted
# andre@titan:~$ kubectl delete crd rayjobs.ray.io --context dev
# customresourcedefinition.apiextensions.k8s.io "rayjobs.ray.io" deleted
# andre@titan:~$ kubectl delete crd rayservices.ray.io --context dev
# customresourcedefinition.apiextensions.k8s.io "rayservices.ray.io" deleted
kuberay = Release(
    "kuberay",
    repository_opts=RepositoryOptsArgs(
        repo="https://ray-project.github.io/kuberay-helm/",
    ),
    chart="kuberay-operator",
    version="1.0.0",
    values={
        "tolerations": [
            {
                "key": "dedicated",
                "value": "gpu-enabled",
                "effect": "NoSchedule",
            },
        ],
    },
    namespace=ray_ns,
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
        depends_on=[gpu_managed_node_groups[0], managed_node_groups[0]],
        # Ignore changes to checksum; bug in pulumi-kubernetes; see:
        # https://github.com/pulumi/pulumi-kubernetes/issues/2649
        # Remove this once that issue is resolved.
        ignore_changes=["checksum"],
    ),
)

# set up TLS
#
# we have a domain registered with AWS and a hosted zone set up in
# Route 53, now we have to request an SSL certificate via the
# AWS certificate manager
hosted_zone = aws.route53.get_zone(name=config.get("domain"))

# "After you write the DNS record or have ACM write the record for you,
# it typically takes DNS 30 minutes to propagate the record, and
# it might take several hours for Amazon to validate it and issue the certificate."
# => this just times out since it takes too long, manually request:
# https://us-west-2.console.aws.amazon.com/acm/home?region=us-west-2
if certificate_arn is None:
    # SSL Cert must be created in us-east-1 unrelated to where the API is deployed.
    aws_us_east_1 = aws.Provider("aws-provider-us-east-1", region="us-east-1")

    # Request ACM certificate
    ssl_cert = aws.acm.Certificate(
        "ssl-cert",
        domain_name=config.get("domain"),
        validation_method="DNS",
        opts=pulumi.ResourceOptions(provider=aws_us_east_1),
    )

    # Create DNS record to prove to ACM that we own the domain
    ssl_cert_validation_dns_record = aws.route53.Record(
        "ssl-cert-validation-dns-record",
        zone_id=hosted_zone.id,
        name=ssl_cert.domain_validation_options.apply(
            lambda options: options[0].resource_record_name
        ),
        type=ssl_cert.domain_validation_options.apply(
            lambda options: options[0].resource_record_type
        ),
        records=[
            ssl_cert.domain_validation_options.apply(
                lambda options: options[0].resource_record_value
            ),
        ],
        ttl=10 * 60,
    )

    # Wait for the certificate validation to succeed
    validated_ssl_certificate = aws.acm.CertificateValidation(
        f"ssl-cert-validation-{config.get('domain')}",
        certificate_arn=ssl_cert.arn,
        validation_record_fqdns=[ssl_cert_validation_dns_record.fqdn],
        opts=pulumi.ResourceOptions(provider=aws_us_east_1),
    )

    certificate_arn = validated_ssl_certificate.certificate_arn

# external-dns
#
# this manages the DNS records in Route 53, and points them to the AWS Load Balancer Controller alb
#
# https://github.com/kubernetes-sigs/external-dns/blob/master/docs/tutorials/aws-load-balancer-controller.md
# https://github.com/lbrlabs/pulumi-external-dns/blob/master/__main__.py
# https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.1/guide/integrations/external_dns/
ns = "external-dns"
external_dns_ns = k8s.core.v1.Namespace(
    f"{ns}",
    metadata={
        "name": ns,
        "labels": {
            "app.kubernetes.io/name": "external-dns",
        },
    },
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
    ),
)

# we need to create a service account that has IAM permissions
# https://github.com/kubernetes-sigs/external-dns/blob/master/docs/tutorials/aws.md#iam-permissions
edns_service_account_name = f"system:serviceaccount:{ns}:external-dns-serviceaccount"

edns_role = aws.iam.Role(
    f"external-dns-role",
    assume_role_policy=pulumi.Output.all(cluster_oidc_arn, cluster_oidc_url).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Federated": args[0],
                        },
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                f"{args[1]}:sub": edns_service_account_name,
                                f"{args[1]}:aud": "sts.amazonaws.com",
                            }
                        },
                    }
                ],
            }
        )
    ),
)

policy_doc = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["route53:ChangeResourceRecordSets"],
            "Resource": ["arn:aws:route53:::hostedzone/*"],
        },
        {
            "Effect": "Allow",
            "Action": ["route53:ListHostedZones", "route53:ListResourceRecordSets"],
            "Resource": ["*"],
        },
    ],
}

edns_policy = aws.iam.RolePolicy(
    f"external-dns-policy",
    role=edns_role,
    policy=json.dumps(policy_doc),
    opts=pulumi.ResourceOptions(
        parent=edns_role,
    ),
)

k8s.core.v1.ServiceAccount(
    "external-dns-serviceaccount",
    metadata={
        "name": "external-dns-serviceaccount",
        "namespace": ns,
        "annotations": {
            "eks.amazonaws.com/role-arn": edns_role.arn.apply(lambda arn: arn)
        },
    },
)

# $ helm install my-release \
#   --set provider=aws \
#   --set aws.zoneType=public \
#   --set txtOwnerId=HOSTED_ZONE_IDENTIFIER \
#   --set domainFilters[0]=HOSTED_ZONE_NAME \
#   bitnami/external-dns
#
# Production configuration
#
# This chart includes a values-production.yaml file where you can find some parameters
# oriented to production configuration in comparison to the regular values.yaml.
# You can use this file instead of the default one.
#
#     Desired number of ExternalDNS replicas:
#
# - replicas: 1
# + replicas: 3

external_dns = k8s.helm.v3.Chart(
    "external-dns",
    k8s.helm.v3.ChartOpts(
        chart="external-dns",
        namespace=ns,
        values={
            "serviceAccount": {
                "name": "external-dns-serviceaccount",
                "create": False,
            },
            "source": "ingress",  # from tutorial
            "provider": "aws",
            "replicas": "2",
            "metrics.enabled": "false",
            "aws.zoneType": "public",
            "txtOwnerId": hosted_zone.id,
            "domainFilters": [config.get("domain")],
        },
        fetch_opts=k8s.helm.v3.FetchOpts(repo="https://charts.bitnami.com/bitnami"),
    ),
    pulumi.ResourceOptions(
        provider=cluster_provider,
        parent=external_dns_ns,
    ),
)

# surprise - new schema, invalid examples for you, for you, for everyone!
# see https://kubernetes.io/docs/concepts/services-networking/ingress/
# https://github.com/kubernetes-sigs/external-dns/blob/master/docs/tutorials/aws-load-balancer-controller.md

# AWS Cognito
#
# https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.1/guide/tasks/cognito_authentication/

# https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html

fireworks_cfg = {
    "FIREWORKS_ENDPOINTS_CLOUD_URL": config.require("fireworks-endpoints-cloud-url"),
}

fireworks_secrets = {
    "FIREWORKS_ENDPOINTS_API_KEY": to_k8s_secret(config, "fireworks-endpoints-api-key"),
}

ray_secret_map = k8s.core.v1.Secret(
    "ray-secrets",
    data={
        "AWS_ACCESS_KEY_ID": to_k8s_secret(config, "aws-access-key-id"),
        "AWS_SECRET_ACCESS_KEY": to_k8s_secret(config, "aws-secret-access-key"),
        **fireworks_secrets,
    },
    metadata={
        "name": "ray-secrets",
        "namespace": "ray",
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cluster],
        provider=cluster_provider,
    ),
)

ray_config_map = k8s.core.v1.ConfigMap(
    "ray-config",
    data={
        "LLM_PROVIDER": config.require("llm-provider"),
        **anyscale_cfg,
        **fireworks_cfg,
    },
    metadata={
        "name": "ray-config",
        "namespace": "ray",
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cluster],
        provider=cluster_provider,
    ),
)

pgsql_cfg = {
    "POSTGRESQL_HOST": config.require("postgresql-host"),
    "POSTGRESQL_PORT": config.get("postgresql-port") or "5432",
    "POSTGRESQL_DB": config.require("postgresql-db"),
}

pgsql_secrets = {
    "POSTGRESQL_USER": to_k8s_secret(config, "postgresql-user"),
    "POSTGRESQL_PASSWORD": to_k8s_secret(config, "postgresql-password"),
}

neo4j_cfg = {
    "NEO4J_GRAPHENEDB_URL": config.require("neo4j-graphenedb-url"),
    "NEO4J_GRAPHENEDB_USER": config.require("neo4j-graphenedb-user"),
}

neo4j_secrets = {
    "NEO4J_GRAPHENEDB_PASSWORD": to_k8s_secret(config, "neo4j-graphenedb-password"),
}

if env == "dev":
    neo4j_secrets |= {
        "NEO4J_CLOUD_RW_API_KEY": to_k8s_secret(config, "neo4j-cloud-rw-api-key")
    }

weaviate_cfg = {
    "WEAVIATE_URL": config.get("weaviate-url")
    or "http://weaviate.weaviate.svc.cluster.local",
}


sendgrid_secrets = {
    "SENDGRID_API_KEY": to_k8s_secret(config, "sendgrid-api-key"),
    "SENDGRID_ASM_GROUP_ID": to_k8s_secret(config, "sendgrid-asm-group-id"),
}

s3_secrets = {
    "S3_AWS_ACCESS_KEY_ID": to_k8s_secret(config, "aws-access-key-id"),
    "S3_AWS_SECRET_ACCESS_KEY": to_k8s_secret(config, "aws-secret-access-key"),
}

launchdarkly_secrets = {
    "LAUNCHDARKLY_SDK_KEY": to_k8s_secret(config, "launchdarkly-sdk-key"),
}

twitter_cfg = {
    "TWITTER_USER_AUTH_CLIENT_ID": config.require("twitter-user-auth-client-id"),
}

twitter_secrets = {
    "TWITTER_APP_AUTH_BEARER_TOKEN": to_k8s_secret(
        config, "twitter-app-auth-bearer-token"
    ),
    "TWITTER_APP_AUTH_CONSUMER_KEY": to_k8s_secret(
        config, "twitter-app-auth-consumer-key"
    ),
    "TWITTER_APP_AUTH_CONSUMER_SECRET": to_k8s_secret(
        config, "twitter-app-auth-consumer-secret"
    ),
}

google_cfg = {
    "GOOGLE_OAUTH_CLIENT_ID": config.require("google-oauth2-client-id"),
    "GOOGLE_DRIVE_SCOPES": " ".join(config.require_object("google-drive-scopes")),
}

google_secrets = {
    "GOOGLE_OAUTH_CLIENT_SECRET": to_k8s_secret(config, "google-oauth2-client-secret"),
}


SECRET_KEYS = [
    "POSTGRESQL_PASSWORD",
    "POSTGRESQL_USER",
]


def get_secret(config_map: k8s.core.v1.Secret, key: str) -> k8s.core.v1.EnvVarArgs:
    return k8s.core.v1.EnvVarArgs(
        name=key,
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name=config_map._name,
                key=key,
            )
        ),
    )


if config.get_bool("devdb-enabled") or False:
    # NB: see note in dev_db about 'prod-clone' naming convention here.
    db_subnet_group = aws.rds.SubnetGroup(
        "prod-clone-db-subnet-group",
        name="prod-clone-db-subnet-group",
        subnet_ids=private_subnet_ids,
    )

    db_sg = aws.ec2.SecurityGroup(
        "prod-clone-db-sg",
        description="DB Access",
        vpc_id=vpc.id,
    )

    # This is allows any system in this VPC to access port 5432 of RDS
    # This is NOT BEST PRACTICES, but Pulumi / AWS was making it near impossible to reference
    # the cluster SGs.  We'll want to address this.
    aws.ec2.SecurityGroupRule(
        f"eks-to-rds-sg",
        type="ingress",
        from_port=5432,
        to_port=5432,
        protocol="tcp",
        cidr_blocks=[vpc_cidr],
        security_group_id=db_sg.id,
        description="EKS to RDS",
    )

    dev_db = aws.rds.Instance(
        # NB: Current dev environment database was cloned from production circa
        # March 2022. There was already anothed database named 'dev' at the time
        # so the dev environment's database was named 'prod-clone'. Renaming it
        # here would result in pulumi deleting the resource (which is
        # undesirable at time of writing).
        "prod-clone-db",
        allocated_storage=100,
        db_name="user_data",
        engine="postgres",
        engine_version="11.19",
        instance_class="db.t3.large",
        username="postgres",
        password=config.require_secret("postgresql-password"),
        db_subnet_group_name="prod-clone-db-subnet-group",
        vpc_security_group_ids=[db_sg.id],
        opts=pulumi.ResourceOptions(protect=True, retain_on_delete=True),
    )


ns = "internal"
internal_ns = k8s.core.v1.Namespace(
    f"{ns}",
    metadata={
        "name": ns,
        "labels": {
            "app.kubernetes.io/name": ns,
        },
    },
    opts=pulumi.ResourceOptions(
        provider=cluster_provider,
    ),
)

internal_config_map = k8s.core.v1.ConfigMap(
    "backend-config-internal",
    data={
        "ENV": env,
        # Required by alembic migrations
        **pgsql_cfg,
    },
    metadata={
        "name": "backend-config-internal",
        "namespace": ns,
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cluster],
        provider=cluster_provider,
    ),
)

internal_secret_map = k8s.core.v1.Secret(
    "backend-secrets-internal",
    data={
        # Required for alembic migrations
        **pgsql_secrets,
    },
    metadata={
        "name": "backend-secrets-internal",
        "namespace": ns,
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cluster],
        provider=cluster_provider,
    ),
)


# https://www.pulumi.com/blog/build-publish-containers-iac/#authenticate-with-temporary-ecr-access-token
def getRegistryInfo(rid):
    creds = aws.ecr.get_credentials(registry_id=rid)
    decoded = base64.b64decode(creds.authorization_token).decode()
    parts = decoded.split(":")
    if len(parts) != 2:
        raise Exception("Invalid credentials")
    return {
        "server": creds.proxy_endpoint,
        "username": parts[0],
        "password": parts[1],
    }


internal_provider = k8s.Provider(
    f"internal-provider",
    enable_server_side_apply=False,
    kubeconfig=cluster.kubeconfig_json,
    namespace="internal",
)


def define_migration_job(
    provider: k8s.Provider = internal_provider,
    config_map: k8s.core.v1.ConfigMap = internal_config_map,
    secret_map: k8s.core.v1.Secret = internal_secret_map,
) -> k8s.batch.v1.Job:
    name = "alembic"
    repo = aws.ecr.Repository(name, force_delete=True)
    image_name = repo.repository_url
    registry_info = repo.registry_id.apply(getRegistryInfo)

    image = Image(
        name,
        image_name=image_name,
        registry=registry_info,
        build=DockerBuildArgs(
            builder_version=BuilderVersion.BUILDER_BUILD_KIT,
            context="./",
            dockerfile="./infra/pgsql/alembic.Dockerfile",
            platform="linux/amd64",
            args={"BUILDKIT_INLINE_CACHE": "1"},
        ),
        skip_push=False,
    )

    job = k8s.batch.v1.Job(
        name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=name,
            labels={"image-sha": image_sha(image.repo_digest)},
        ),
        spec=k8s.batch.v1.JobSpecArgs(
            backoff_limit=4,
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={"app": name},  # | datadog_labels(env, name),
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            image=image.image_name,
                            name=name,
                            image_pull_policy="Always",
                            env_from=[
                                k8s.core.v1.EnvFromSourceArgs(
                                    config_map_ref=k8s.core.v1.ConfigMapEnvSourceArgs(
                                        name=config_map.metadata.name,
                                    )
                                )
                            ],
                            env=[
                                *(get_secret(secret_map, x) for x in SECRET_KEYS),
                            ],
                        )
                    ],
                    restart_policy="Never",
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=provider,
            delete_before_replace=True,
            depends_on=[image],
            replace_on_changes=["metadata.labels"],
        ),
    )
    return job


# The alembic job needs to run before any other services/endpoints/apps get
# updated, since they will depend on the DBs being already updated
alembic_job = define_migration_job()


def define_daemon_set(
    name,
    provider=internal_provider,
    config_map=internal_config_map,
    tolerations=[],
    node_selector={},
):
    repo = aws.ecr.Repository(name, force_delete=True)
    image_name = repo.repository_url
    registry_info = repo.registry_id.apply(getRegistryInfo)

    image = Image(
        name,
        image_name=image_name,
        registry=registry_info,
        build=DockerBuildArgs(
            builder_version=BuilderVersion.BUILDER_BUILD_KIT,
            context="src",
            dockerfile=os.path.abspath(f"src/{name}.Dockerfile"),
            platform="linux/amd64",
            # cache_from=CacheFromArgs(
            #    images=[image_name.apply(lambda x: f"{x}:latest")]
            # ),
            args={"BUILDKIT_INLINE_CACHE": "1"},
        ),
        skip_push=False,
    )
    # export image name for debugging purposes
    pulumi.export(f"{name}-image-name", image.image_name)

    k8s.apps.v1.DaemonSet(
        name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=name,
            labels={
                "app": name,
            },
        ),
        spec=k8s.apps.v1.DaemonSetSpecArgs(
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={
                    "app": name,
                },
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={"app": name},  # | datadog_labels(env, name),
                    # annotations=datadog_annotations(enable_apm=True),
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    tolerations=tolerations,
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name=name,
                            image=image.image_name.apply(lambda x: x),
                            image_pull_policy="Always",
                            stdin=True,
                            tty=True,
                            env_from=[
                                k8s.core.v1.EnvFromSourceArgs(
                                    config_map_ref=k8s.core.v1.ConfigMapEnvSourceArgs(
                                        name=config_map._name,
                                    )
                                )
                            ],
                        )
                    ],
                    node_selector=node_selector,
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(provider=provider),
    )


# -----------------------------------------------------------------------------

# The DataDog cluster agent is responsible for collecting metrics and logs from
# annotated pods in the cluster and sending them to DataDog.
#
# See `datadog_labels` and `datadog_annotations` for details.
# declare_datadog_cluster_agent(
#    api_key=config.require_secret("datadog-api-key"),
#    cluster=cluster,
#    k8s_provider=cluster_provider,
# )

# Region where AWS resources are created.
aws_region = aws_config.require("region")
aws_provider = aws.Provider(
    f"aws-provider-{aws_region}",
    region=aws_region,
)

# NB(bruno): This is hackyAF... The cluster creation declaration should have
# been given an aws provider that explicitly determines its region but adding
# it now generates quite the scary diff — I'd rather make that change by itself
# at a later point in time.
#
# When created with an aws.Provider, `cluster.core.aws_provider` is non-null.
# This is extremely handy to avoid having to pass the provider around to all
# of our resource declaration helpers that already accept eks.Cluster as an
# argument.
#
# For now, this allows "attaching" the aws provider to the eks.Cluster object.
cluster.core.aws_provider = aws_provider

# Application Load Balancer Controller (ALB) is responsible for creating and
# managing EC2 Application Load Balancers (ALBs) for annotated Kubernetes
# Ingress resources.
#
# See `declare_http_server(exposed_as=PublicHttpServer(...))` for details on
# how to publicly expose an HTTP server via ALB.
declare_alb_controller(
    cluster=cluster,
    k8s_provider=cluster_provider,
)

# --- Shared images and backing ECR repositories

worker_image = declare_image_in_ecr(
    name="worker",
    aws_provider=aws_provider,
    dockerfile="./infra/workers/worker.Dockerfile",
).image

server_image = declare_image_in_ecr(
    name="server",
    aws_provider=aws_provider,
    dockerfile="./infra/http_servers/http_server.Dockerfile",
).image

# --- Cognito IDP setup

# TODO: this resource should be managed by pulumi
user_pool = aws.cognito.UserPool.get(
    resource_name=config.require("cognito-userpool-name"),
    id=config.require("cognito-userpool-id"),
)

# --- SQS queues
account_deletions = declare_queue_with_dlq(
    queue_name="account_deletions",
    aws_provider=cluster.core.aws_provider,
)

# --- S3 buckets

# TODO: this bucket should be managed by pulumi
userfiles_bucket_name = config.require("user-files-bucket")

# --- Public app zone ---------------------------------------------------------
# Apps exposed to inbound traffic from the internet.

public_app_zone = declare_app_zone(
    name="public",
    cluster=cluster,
    config_kv_pairs={
        "ENV": env,
        **pgsql_cfg,
        **weaviate_cfg,
        **neo4j_cfg,
        **fireworks_cfg,
        "LLM_PROVIDER": config.require("llm-provider"),
        "DEFAULT_EMBEDDING_ENGINE": config.require("default-embedding-engine"),
        "S3_BUCKET_USERFILES": userfiles_bucket_name,
        "COGNITO_USERPOOL_ID": user_pool.id,
        "COGNITO_REGION": config.require("cognito-region"),
        "COGNITO_APP_CLIENT_ID": config.require("cognito-app-client-id"),
        "ALLOW_ORIGINS": ",".join(config.require_object("allow-origins")),
        "ALLOW_ADMIN": ",".join(config.require_object("allow-admin")),
        **twitter_cfg,
        **google_cfg,
    },
    secret_kv_pairs={
        **pgsql_secrets,
        **sendgrid_secrets,
        **launchdarkly_secrets,
        **neo4j_secrets,
        **fireworks_secrets,
        # TODO: Drop these in favor of IAM policies attached to service accounts.
        **s3_secrets,
        **google_secrets,
    },
)

# Public HTTP API
declare_http_server(
    server_name="api",
    zone=public_app_zone,
    env=env,
    dockerfile_or_image=server_image,
    replicas=2 if env == "prod" else 1,
    exposed_as=PublicHttpServer(
        subdomains=["api", "backend"],
        domain=config.require("domain"),  # e.g. "staging.re-collect.cloud"
        certificate_arn=certificate_arn,
    ),
    is_allowed_to=[
        # TODO: Figure out all required permissions for S3 and list here.
    ],
)

# --- Private app zone --------------------------------------------------------
# Apps with outbound access to the internet but not exposed to inbound traffic.

private_app_zone = declare_app_zone(
    name="private",
    cluster=cluster,
    config_kv_pairs={
        "ENV": env,
        **pgsql_cfg,
        **weaviate_cfg,
        **neo4j_cfg,
        **fireworks_cfg,
        "LLM_PROVIDER": config.require("llm-provider"),
        "S3_BUCKET_USERFILES": userfiles_bucket_name,
        "COGNITO_USERPOOL_ID": user_pool.id,
        "PDF_MAX_FILE_SIZE_IN_MIB": config.get("pdf-max-file-size-in-mib") or "10",
        **twitter_cfg,
        **google_cfg,
    },
    secret_kv_pairs={
        **pgsql_secrets,
        **sendgrid_secrets,
        **launchdarkly_secrets,
        **neo4j_secrets,
        **fireworks_secrets,
        **twitter_secrets,
        **google_secrets,
    },
)

declare_worker(
    worker_name="backup_scheduler",
    zone=private_app_zone,
    env=env,
    dockerfile_or_image=worker_image,
)

declare_worker(
    worker_name="account_deleter",
    zone=private_app_zone,
    env=env,
    dockerfile_or_image=worker_image,
    is_allowed_to=[
        consume_from_queues(
            account_deletions.main_queue,
            account_deletions.deadletter_queue,
        ),
        list_objects_in_bucket(userfiles_bucket_name),
        delete_objects_in_bucket(userfiles_bucket_name),
        delete_users_in_pool(user_pool),
    ],
)

declare_worker(
    worker_name="recurring_import_dispatcher",
    zone=private_app_zone,
    env=env,
    dockerfile_or_image=worker_image,
)

declare_worker(
    worker_name="recurring_import_processor",
    zone=private_app_zone,
    env=env,
    dockerfile_or_image=worker_image,
    is_allowed_to=[
        put_objects_in_bucket(userfiles_bucket_name),
    ],
)


# --- Tools zone --------------------------------------------------------------
# Non-critical apps to aid cluster administration and troubleshooting.

tool_zone = declare_app_zone(
    name="tools",
    cluster=cluster,
    config_kv_pairs={
        "ENV": env,
        **pgsql_cfg,
    },
    secret_kv_pairs={
        # Empty secret maps are annoyingly recreated every deployment by pulumi.
        # Delete this placehold once there are actual secrets to store here.
        "placeholder": base64_str("empty"),
    },
)

declare_dbproxy(zone=tool_zone)
