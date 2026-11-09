"""Tests for AWS ECS (Fargate) generation."""

import json
import unittest

from clouddeploy.aws import (
    aws_commands,
    aws_task_definition,
    aws_task_definition_json,
    ecr_repository_uri,
)
from clouddeploy.config import validate_config
from tests.test_config import valid_config


class TestAwsTaskDefinition(unittest.TestCase):
    def setUp(self):
        self.cfg = validate_config(valid_config())

    def test_fargate_fields(self):
        task_def = aws_task_definition(self.cfg)
        self.assertEqual(task_def["family"], "hello-cloud")
        self.assertEqual(task_def["networkMode"], "awsvpc")
        self.assertEqual(task_def["requiresCompatibilities"], ["FARGATE"])
        self.assertEqual(task_def["cpu"], "512")
        self.assertEqual(task_def["memory"], "1024")
        self.assertEqual(
            task_def["executionRoleArn"],
            "arn:aws:iam::123456789012:role/ecsTaskExecutionRole")

    def test_container_definition(self):
        container = aws_task_definition(self.cfg)["containerDefinitions"][0]
        self.assertEqual(container["name"], "hello-cloud")
        self.assertTrue(container["essential"])
        self.assertEqual(container["portMappings"],
                         [{"containerPort": 8080, "protocol": "tcp"}])
        self.assertIn({"name": "MESSAGE", "value": "hi"},
                      container["environment"])
        self.assertEqual(container["logConfiguration"]["logDriver"], "awslogs")
        self.assertEqual(
            container["logConfiguration"]["options"]["awslogs-region"],
            "ca-central-1")

    def test_image_points_at_ecr(self):
        container = aws_task_definition(self.cfg)["containerDefinitions"][0]
        self.assertEqual(
            container["image"],
            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/hello-cloud:latest")

    def test_task_role_included_when_configured(self):
        cfg = valid_config()
        cfg["aws"]["task_role_arn"] = "arn:aws:iam::123456789012:role/myTaskRole"
        task_def = aws_task_definition(validate_config(cfg))
        self.assertEqual(task_def["taskRoleArn"],
                         "arn:aws:iam::123456789012:role/myTaskRole")

    def test_task_role_absent_by_default(self):
        self.assertNotIn("taskRoleArn", aws_task_definition(self.cfg))

    def test_json_round_trips(self):
        parsed = json.loads(aws_task_definition_json(self.cfg))
        self.assertEqual(parsed, aws_task_definition(self.cfg))

    def test_ecr_uri(self):
        self.assertEqual(
            ecr_repository_uri(self.cfg),
            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/hello-cloud")

    def test_missing_aws_section_raises(self):
        cfg = valid_config()
        del cfg["aws"]
        with self.assertRaises(Exception) as ctx:
            aws_task_definition(validate_config(cfg))
        self.assertIn("aws", str(ctx.exception))


class TestAwsCommands(unittest.TestCase):
    def test_commands_cover_full_flow(self):
        joined = "\n".join(aws_commands(validate_config(valid_config())))
        self.assertIn("aws ecr create-repository --repository-name hello-cloud",
                      joined)
        self.assertIn("aws ecr get-login-password --region ca-central-1",
                      joined)
        self.assertIn("docker push 123456789012.dkr.ecr.ca-central-1"
                      ".amazonaws.com/hello-cloud:latest", joined)
        self.assertIn("aws ecs register-task-definition --cli-input-json "
                      "file://task-definition.json", joined)
        self.assertIn("aws ecs create-cluster --cluster-name "
                      "clouddeploy-cluster", joined)
        self.assertIn("aws ecs create-service --cluster clouddeploy-cluster "
                      "--service-name hello-cloud", joined)
        self.assertIn("--launch-type FARGATE", joined)
        self.assertIn("subnets=[subnet-0123456789abcdef0]", joined)
        self.assertIn("securityGroups=[sg-0123456789abcdef0]", joined)
        self.assertIn("assignPublicIp=ENABLED", joined)

    def test_desired_count_matches_replicas(self):
        cfg = valid_config()
        cfg["app"]["replicas"] = 3
        joined = "\n".join(aws_commands(validate_config(cfg)))
        self.assertIn("--desired-count 3", joined)


if __name__ == "__main__":
    unittest.main()
