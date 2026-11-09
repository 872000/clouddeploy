"""Tests for Azure Container Apps generation."""

import json
import unittest

from clouddeploy.azure import azure_bicep, azure_commands
from clouddeploy.config import validate_config
from tests.test_config import valid_config


class TestAzureCommands(unittest.TestCase):
    def setUp(self):
        self.cfg = validate_config(valid_config())

    def test_creates_resource_group_environment_and_app(self):
        commands = azure_commands(self.cfg)
        joined = "\n".join(commands)
        self.assertIn("az group create --name clouddeploy-rg "
                      "--location canadacentral", joined)
        self.assertIn("az containerapp env create --name clouddeploy-env "
                      "--resource-group clouddeploy-rg", joined)
        self.assertIn("az containerapp create --name hello-cloud", joined)
        self.assertIn("--image hello-cloud:latest", joined)
        self.assertIn("--target-port 8080", joined)
        self.assertIn("--cpu 0.5", joined)
        self.assertIn("--memory 1Gi", joined)
        self.assertIn("--ingress external", joined)

    def test_env_vars_included(self):
        joined = "\n".join(azure_commands(self.cfg))
        self.assertIn("MESSAGE=hi", joined)
        self.assertIn("APP_NAME=hello-cloud", joined)

    def test_replicas_included(self):
        joined = "\n".join(azure_commands(self.cfg))
        self.assertIn("--min-replicas 1 --max-replicas 1", joined)

    def test_final_command_shows_fqdn(self):
        commands = azure_commands(self.cfg)
        self.assertIn("az containerapp show", commands[-1])
        self.assertIn("ingress.fqdn", commands[-1])

    def test_container_app_override_used(self):
        cfg = valid_config()
        cfg["azure"]["container_app"] = "hello-web"
        commands = azure_commands(validate_config(cfg))
        self.assertIn("az containerapp create --name hello-web",
                      "\n".join(commands))

    def test_no_env_vars_means_no_env_flag(self):
        cfg = valid_config()
        cfg["app"]["env"] = {}
        joined = "\n".join(azure_commands(validate_config(cfg)))
        self.assertNotIn("--env-vars", joined)

    def test_missing_azure_section_raises(self):
        cfg = valid_config()
        del cfg["azure"]
        with self.assertRaises(Exception) as ctx:
            azure_commands(validate_config(cfg))
        self.assertIn("azure", str(ctx.exception))


class TestAzureBicep(unittest.TestCase):
    def test_bicep_contains_resources(self):
        bicep = azure_bicep(validate_config(valid_config()))
        self.assertIn("Microsoft.App/managedEnvironments", bicep)
        self.assertIn("Microsoft.App/containerApps", bicep)
        self.assertIn("hello-cloud", bicep)
        self.assertIn("canadacentral", bicep)
        self.assertIn("param targetPort int = 8080", bicep)
        self.assertIn("targetPort: targetPort", bicep)
        self.assertIn("memory: '1Gi'", bicep)
        self.assertIn("MESSAGE", bicep)
        self.assertIn("output fqdn", bicep)

    def test_bicep_env_block_absent_without_env(self):
        cfg = valid_config()
        cfg["app"]["env"] = {}
        self.assertNotIn("env: [", azure_bicep(validate_config(cfg)))


if __name__ == "__main__":
    unittest.main()
