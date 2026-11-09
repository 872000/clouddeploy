"""Tests for the CLI, including the dry-run guarantee: in dry-run mode
no subprocess is ever started."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from clouddeploy.cli import main
from tests.test_config import valid_config

import yaml

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "sample.yaml"


def run_cli(argv):
    """Run main(argv), capturing stdout/stderr; return (exit_code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCliValidate(unittest.TestCase):
    def test_validate_sample_config(self):
        code, out, _ = run_cli(["validate", "-c", str(SAMPLE_PATH)])
        self.assertEqual(code, 0)
        self.assertIn("config OK", out)
        self.assertIn("hello-cloud", out)

    def test_validate_bad_config_exits_2(self):
        bad = valid_config()
        bad["app"]["port"] = 99999
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                        delete=False) as handle:
            yaml.safe_dump(bad, handle)
            path = handle.name
        code, _, err = run_cli(["validate", "-c", path])
        self.assertEqual(code, 2)
        self.assertIn("app.port", err)

    def test_validate_missing_file_exits_2(self):
        code, _, err = run_cli(["validate", "-c", "/nope/missing.yaml"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read config", err)


class TestCliDryRun(unittest.TestCase):
    def test_build_dry_run_prints_docker_command_and_runs_nothing(self):
        with mock.patch("clouddeploy.deploy.subprocess.run") as run:
            code, out, _ = run_cli(["build", "-c", str(SAMPLE_PATH)])
        self.assertEqual(code, 0)
        self.assertIn("docker build -t hello-cloud:latest sample-app", out)
        self.assertIn("dry-run", out)
        run.assert_not_called()

    def test_build_tag_override(self):
        code, out, _ = run_cli(["build", "-c", str(SAMPLE_PATH),
                                "--tag", "hello-cloud:v2"])
        self.assertEqual(code, 0)
        self.assertIn("docker build -t hello-cloud:v2", out)

    def test_plan_azure_prints_commands_and_bicep(self):
        code, out, _ = run_cli(["plan", "-c", str(SAMPLE_PATH),
                                "--provider", "azure"])
        self.assertEqual(code, 0)
        self.assertIn("az containerapp create", out)
        self.assertIn("Microsoft.App/containerApps", out)

    def test_plan_aws_prints_task_definition_and_commands(self):
        code, out, _ = run_cli(["plan", "-c", str(SAMPLE_PATH),
                                "--provider", "aws"])
        self.assertEqual(code, 0)
        self.assertIn("aws ecs create-service", out)
        self.assertIn('"family": "hello-cloud"', out)
        self.assertIn('"networkMode": "awsvpc"', out)

    def test_plan_writes_manifests_to_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli(["plan", "-c", str(SAMPLE_PATH),
                                    "--provider", "aws",
                                    "--output-dir", tmp])
            self.assertEqual(code, 0)
            task_def = json.loads((Path(tmp) / "task-definition.json").read_text())
            self.assertEqual(task_def["family"], "hello-cloud")

    def test_deploy_dry_run_runs_nothing(self):
        with mock.patch("clouddeploy.deploy.subprocess.run") as run:
            code, out, _ = run_cli(["deploy", "-c", str(SAMPLE_PATH),
                                    "--provider", "azure"])
        self.assertEqual(code, 0)
        self.assertIn("az containerapp create", out)
        run.assert_not_called()

    def test_deploy_aws_dry_run_runs_nothing(self):
        with mock.patch("clouddeploy.deploy.subprocess.run") as run:
            code, out, _ = run_cli(["deploy", "-c", str(SAMPLE_PATH),
                                    "--provider", "aws"])
        self.assertEqual(code, 0)
        self.assertIn("aws ecs create-service", out)
        run.assert_not_called()

    def test_deploy_missing_provider_section_exits_2(self):
        cfg = valid_config()
        del cfg["aws"]
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                        delete=False) as handle:
            yaml.safe_dump(cfg, handle)
            path = handle.name
        code, _, err = run_cli(["deploy", "-c", path, "--provider", "aws"])
        self.assertEqual(code, 2)
        self.assertIn("no 'aws' section", err)


class TestCliExecuteMode(unittest.TestCase):
    def test_execute_mode_calls_subprocess(self):
        fake = mock.Mock(returncode=0)
        with mock.patch("clouddeploy.deploy.subprocess.run",
                        return_value=fake) as run:
            code, out, _ = run_cli(["build", "-c", str(SAMPLE_PATH),
                                    "--execute"])
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertIn("docker build", run.call_args.args[0])

    def test_execute_mode_stops_on_failure(self):
        fake = mock.Mock(returncode=1)
        with mock.patch("clouddeploy.deploy.subprocess.run",
                        return_value=fake) as run:
            code, _, err = run_cli(["deploy", "-c", str(SAMPLE_PATH),
                                    "--provider", "azure", "--execute"])
        self.assertEqual(code, 1)
        self.assertIn("failed with exit code 1", err)
        self.assertEqual(run.call_count, 1)  # stopped at the first command


if __name__ == "__main__":
    unittest.main()
