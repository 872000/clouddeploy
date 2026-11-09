"""Tests for config parsing and validation."""

import copy
import tempfile
import unittest
from pathlib import Path

from clouddeploy.config import (
    ConfigError,
    _parse_simple_yaml,
    load_config,
    validate_config,
)


def valid_config():
    return {
        "app": {
            "name": "hello-cloud",
            "image": "hello-cloud:latest",
            "port": 8080,
            "cpu": 0.5,
            "memory": "1Gi",
            "replicas": 1,
            "context": "sample-app",
            "env": {"MESSAGE": "hi", "APP_NAME": "hello-cloud"},
        },
        "azure": {
            "resource_group": "clouddeploy-rg",
            "location": "canadacentral",
            "environment": "clouddeploy-env",
        },
        "aws": {
            "region": "ca-central-1",
            "cluster": "clouddeploy-cluster",
            "service": "hello-cloud",
            "execution_role_arn": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
            "subnets": ["subnet-0123456789abcdef0"],
            "security_groups": ["sg-0123456789abcdef0"],
        },
    }


class TestValidConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = validate_config(valid_config())
        self.assertEqual(cfg.app.name, "hello-cloud")
        self.assertEqual(cfg.app.memory_mib, 1024)
        self.assertEqual(cfg.app.fargate_cpu_units, "512")
        self.assertEqual(cfg.app.fargate_memory, "1024")
        self.assertEqual(cfg.app.memory, "1Gi")
        self.assertEqual(cfg.aws.account_id, "123456789012")
        self.assertEqual(cfg.azure.app_name("hello-cloud"), "hello-cloud")

    def test_memory_variants(self):
        for raw, expected_mib, cpu in (("512Mi", 512, 0.25),
                                       ("2Gi", 2048, 1.0),
                                       (1024, 1024, 0.5)):
            cfg = valid_config()
            cfg["app"]["memory"] = raw
            cfg["app"]["cpu"] = cpu
            self.assertEqual(validate_config(cfg).app.memory_mib, expected_mib)

    def test_azure_only_config_passes(self):
        cfg = valid_config()
        del cfg["aws"]
        self.assertIsNone(validate_config(cfg).aws)

    def test_aws_only_config_passes(self):
        cfg = valid_config()
        del cfg["azure"]
        self.assertIsNone(validate_config(cfg).azure)

    def test_azure_container_app_override(self):
        cfg = valid_config()
        cfg["azure"]["container_app"] = "hello-web"
        self.assertEqual(validate_config(cfg).azure.app_name("hello-cloud"),
                         "hello-web")

    def test_load_sample_yaml(self):
        path = Path(__file__).resolve().parent.parent / "examples" / "sample.yaml"
        cfg = load_config(str(path))
        self.assertEqual(cfg.app.name, "hello-cloud")
        self.assertEqual(cfg.azure.location, "canadacentral")
        self.assertEqual(cfg.aws.region, "ca-central-1")


class TestInvalidConfig(unittest.TestCase):
    def assert_invalid(self, raw, fragment):
        with self.assertRaises(ConfigError) as ctx:
            validate_config(raw)
        self.assertIn(fragment, str(ctx.exception))

    def test_missing_app(self):
        self.assert_invalid({"azure": {}}, "missing required 'app' section")

    def test_no_provider(self):
        cfg = valid_config()
        del cfg["azure"]
        del cfg["aws"]
        self.assert_invalid(cfg, "at least one of 'azure' or 'aws'")

    def test_unknown_top_level_key(self):
        cfg = valid_config()
        cfg["gcp"] = {}
        self.assert_invalid(cfg, "unknown top-level key 'gcp'")

    def test_bad_app_name_uppercase(self):
        cfg = valid_config()
        cfg["app"]["name"] = "HelloCloud"
        self.assert_invalid(cfg, "app.name")

    def test_bad_app_name_leading_hyphen(self):
        cfg = valid_config()
        cfg["app"]["name"] = "-hello"
        self.assert_invalid(cfg, "app.name")

    def test_missing_image(self):
        cfg = valid_config()
        del cfg["app"]["image"]
        self.assert_invalid(cfg, "app.image")

    def test_bad_port_zero(self):
        cfg = valid_config()
        cfg["app"]["port"] = 0
        self.assert_invalid(cfg, "app.port")

    def test_bad_port_too_large(self):
        cfg = valid_config()
        cfg["app"]["port"] = 70000
        self.assert_invalid(cfg, "app.port")

    def test_bad_port_string(self):
        cfg = valid_config()
        cfg["app"]["port"] = "8080"
        self.assert_invalid(cfg, "app.port")

    def test_bad_cpu_value(self):
        cfg = valid_config()
        cfg["app"]["cpu"] = 0.75
        self.assert_invalid(cfg, "app.cpu")

    def test_bad_memory_format(self):
        cfg = valid_config()
        cfg["app"]["memory"] = "1GB"
        self.assert_invalid(cfg, "app.memory")

    def test_bad_memory_zero(self):
        cfg = valid_config()
        cfg["app"]["memory"] = "0Gi"
        self.assert_invalid(cfg, "app.memory")

    def test_invalid_fargate_combo(self):
        cfg = valid_config()
        cfg["app"]["cpu"] = 0.25
        cfg["app"]["memory"] = "4Gi"
        self.assert_invalid(cfg, "not valid with cpu 0.25")

    def test_bad_env_key(self):
        cfg = valid_config()
        cfg["app"]["env"] = {"1BAD": "x"}
        self.assert_invalid(cfg, "app.env")

    def test_bad_env_value_type(self):
        cfg = valid_config()
        cfg["app"]["env"] = {"MESSAGE": 42}
        self.assert_invalid(cfg, "app.env.MESSAGE")

    def test_bad_replicas(self):
        cfg = valid_config()
        cfg["app"]["replicas"] = 0
        self.assert_invalid(cfg, "app.replicas")

    def test_bad_aws_region(self):
        cfg = valid_config()
        cfg["aws"]["region"] = "nowhere-1"
        self.assert_invalid(cfg, "aws.region")

    def test_bad_role_arn(self):
        cfg = valid_config()
        cfg["aws"]["execution_role_arn"] = "not-an-arn"
        self.assert_invalid(cfg, "aws.execution_role_arn")

    def test_empty_subnets(self):
        cfg = valid_config()
        cfg["aws"]["subnets"] = []
        self.assert_invalid(cfg, "aws.subnets")

    def test_bad_subnet_id(self):
        cfg = valid_config()
        cfg["aws"]["subnets"] = ["subnet-xyz"]
        self.assert_invalid(cfg, "aws.subnets")

    def test_bad_assign_public_ip(self):
        cfg = valid_config()
        cfg["aws"]["assign_public_ip"] = "MAYBE"
        self.assert_invalid(cfg, "aws.assign_public_ip")

    def test_unknown_app_key(self):
        cfg = valid_config()
        cfg["app"]["flavour"] = "vanilla"
        self.assert_invalid(cfg, "unknown key 'flavour'")

    def test_multiple_errors_reported_together(self):
        cfg = valid_config()
        cfg["app"]["port"] = 0
        cfg["app"]["name"] = "BAD NAME"
        cfg["aws"]["region"] = "bad"
        with self.assertRaises(ConfigError) as ctx:
            validate_config(cfg)
        message = str(ctx.exception)
        self.assertIn("app.port", message)
        self.assertIn("app.name", message)
        self.assertIn("aws.region", message)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/clouddeploy.yaml")

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                        delete=False) as handle:
            handle.write("")
            path = handle.name
        with self.assertRaises(ConfigError):
            load_config(path)


class TestFallbackYamlParser(unittest.TestCase):
    """The built-in parser (used when PyYAML is absent) must handle the
    sample config's YAML subset."""

    SAMPLE = """
app:
  name: hello-cloud
  image: hello-cloud:latest
  port: 8080
  cpu: 0.5
  memory: 1Gi
  replicas: 2
  env:
    MESSAGE: "Hello there"
    DEBUG: "false"
aws:
  region: ca-central-1
  subnets:
    - subnet-aaaa
    - subnet-bbbb
  security_groups: [sg-cccc, sg-dddd]
"""

    def test_parses_nested_maps_lists_and_scalars(self):
        parsed = _parse_simple_yaml(self.SAMPLE, "test.yaml")
        self.assertEqual(parsed["app"]["name"], "hello-cloud")
        self.assertEqual(parsed["app"]["port"], 8080)
        self.assertEqual(parsed["app"]["cpu"], 0.5)
        self.assertEqual(parsed["app"]["replicas"], 2)
        self.assertEqual(parsed["app"]["env"]["MESSAGE"], "Hello there")
        self.assertEqual(parsed["aws"]["subnets"],
                         ["subnet-aaaa", "subnet-bbbb"])
        self.assertEqual(parsed["aws"]["security_groups"],
                         ["sg-cccc", "sg-dddd"])

    def test_rejects_tabs(self):
        with self.assertRaises(ConfigError):
            _parse_simple_yaml("app:\n\tname: x\n", "test.yaml")

    def test_empty_is_error(self):
        with self.assertRaises(ConfigError):
            _parse_simple_yaml("\n# only a comment\n", "test.yaml")


if __name__ == "__main__":
    unittest.main()
