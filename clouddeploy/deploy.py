"""Build / plan / deploy orchestration.

Dry-run is the default everywhere: commands and manifests are printed and
nothing is executed. Passing ``execute=True`` runs each command with
``subprocess`` and stops on the first failure.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from . import aws as aws_mod
from . import azure as azure_mod
from .config import ConfigError


def docker_build_command(cfg, tag=None):
    """Return the ``docker build`` command (shell string) for the app."""
    image = tag or cfg.app.image
    return f"docker build -t {shlex.quote(image)} {shlex.quote(cfg.app.context)}"


def run_commands(commands, dry_run=True):
    """Print each command; execute them only when ``dry_run`` is False.

    Returns 0 on success. In execute mode, returns the failing command's
    exit code (or 1) and stops at the first failure.
    """
    for command in commands:
        print(f"$ {command}")
        if dry_run:
            continue
        print(f"  -> running (execute mode)", file=sys.stderr)
        result = subprocess.run(command, shell=True)
        if result.returncode != 0:
            print(f"error: command failed with exit code {result.returncode}: "
                  f"{command}", file=sys.stderr)
            return result.returncode or 1
    if dry_run:
        print("\n(dry-run: nothing was executed. Re-run with --execute to "
              "run these commands for real.)")
    return 0


def plan(cfg, provider):
    """Return the (title, body) pairs describing a deployment plan."""
    if provider == "azure":
        return [
            ("Azure CLI commands", "\n".join(azure_mod.azure_commands(cfg))),
            ("Bicep template (main.bicep)", azure_mod.azure_bicep(cfg)),
        ]
    if provider == "aws":
        return [
            ("ECS task definition (task-definition.json)",
             aws_mod.aws_task_definition_json(cfg)),
            ("AWS CLI commands", "\n".join(aws_mod.aws_commands(cfg))),
        ]
    raise ConfigError(f"unknown provider {provider!r} (expected 'azure' or 'aws')")


def deploy_commands(cfg, provider):
    """Return the commands a real deploy would run, in order."""
    if provider == "azure":
        build = docker_build_command(cfg)
        return [build] + azure_mod.azure_commands(cfg)
    if provider == "aws":
        return aws_mod.aws_commands(cfg)
    raise ConfigError(f"unknown provider {provider!r} (expected 'azure' or 'aws')")
