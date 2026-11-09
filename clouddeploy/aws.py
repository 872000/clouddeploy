"""AWS ECS (Fargate) deployment generation.

Produces an ECS task definition (JSON) and the ``aws`` CLI commands that
register it and create the cluster/service. Nothing here talks to AWS;
:func:`clouddeploy.deploy.run_commands` executes the commands only when the
user passes ``--execute``.
"""

from __future__ import annotations

import json
import shlex


def _q(value):
    return shlex.quote(str(value))


def ecr_repository_uri(cfg):
    """``<account>.dkr.ecr.<region>.amazonaws.com/<app-name>``."""
    aws = cfg.require_provider("aws")
    return (f"{aws.account_id}.dkr.ecr.{aws.region}.amazonaws.com/"
            f"{cfg.app.name}")


def aws_task_definition(cfg):
    """Return the ECS task definition dict for the app (Fargate, awsvpc)."""
    app = cfg.app
    aws = cfg.require_provider("aws")
    task_definition = {
        "family": app.name,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": app.fargate_cpu_units,
        "memory": app.fargate_memory,
        "executionRoleArn": aws.execution_role_arn,
        "containerDefinitions": [
            {
                "name": app.name,
                "image": f"{ecr_repository_uri(cfg)}:latest",
                "essential": True,
                "portMappings": [
                    {"containerPort": app.port, "protocol": "tcp"}
                ],
                "environment": [
                    {"name": key, "value": value}
                    for key, value in app.env.items()
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": f"/ecs/{app.name}",
                        "awslogs-region": aws.region,
                        "awslogs-stream-prefix": "ecs",
                    },
                },
            }
        ],
    }
    if aws.task_role_arn:
        task_definition["taskRoleArn"] = aws.task_role_arn
    return task_definition


def aws_task_definition_json(cfg):
    """Return the task definition as a pretty-printed JSON string."""
    return json.dumps(aws_task_definition(cfg), indent=2) + "\n"


def aws_commands(cfg, task_definition_path="task-definition.json"):
    """Return the ordered ``aws``/``docker`` CLI commands (as shell strings)
    that push the image to ECR and run it as an ECS Fargate service."""
    app = cfg.app
    aws = cfg.require_provider("aws")
    repo = ecr_repository_uri(cfg)
    subnets = ",".join(aws.subnets)
    security_groups = ",".join(aws.security_groups)
    network_config = (
        "awsvpcConfiguration={"
        f"subnets=[{subnets}],securityGroups=[{security_groups}],"
        f"assignPublicIp={aws.assign_public_ip}}}"
    )
    return [
        # ECR repository (fails harmlessly if it already exists).
        f"aws ecr create-repository --repository-name {_q(app.name)} "
        f"--region {_q(aws.region)}",
        f"aws ecr get-login-password --region {_q(aws.region)} | "
        f"docker login --username AWS --password-stdin {_q(repo)}",
        f"docker tag {_q(app.image)} {_q(repo + ':latest')}",
        f"docker push {_q(repo + ':latest')}",
        f"aws ecs register-task-definition --cli-input-json "
        f"file://{_q(task_definition_path)} --region {_q(aws.region)}",
        f"aws ecs create-cluster --cluster-name {_q(aws.cluster)} "
        f"--region {_q(aws.region)}",
        f"aws ecs create-service --cluster {_q(aws.cluster)} "
        f"--service-name {_q(aws.service)} "
        f"--task-definition {_q(app.name)} --desired-count {app.replicas} "
        f"--launch-type FARGATE --platform-version LATEST "
        f"--network-configuration {_q(network_config)} "
        f"--region {_q(aws.region)}",
    ]
