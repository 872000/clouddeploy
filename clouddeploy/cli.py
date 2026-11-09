"""Command-line interface for clouddeploy.

Usage::

    clouddeploy -c config.yaml validate
    clouddeploy -c config.yaml build [--dry-run | --execute] [--tag TAG]
    clouddeploy -c config.yaml plan --provider azure|aws [--output-dir DIR]
    clouddeploy -c config.yaml deploy --provider azure|aws [--dry-run | --execute]

``--dry-run`` is the default: commands and manifests are printed and nothing
is executed. Pass ``--execute`` to actually run them (needs Docker and the
``az`` / ``aws`` CLIs, plus your own cloud credentials).
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, aws as aws_mod, azure as azure_mod
from .config import ConfigError, load_config
from .deploy import deploy_commands, docker_build_command, plan, run_commands


def _add_common(parser):
    parser.add_argument("-c", "--config", default=argparse.SUPPRESS,
                        help="path to the YAML config file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true",
                       default=True,
                       help="print commands without executing them (default)")
    group.add_argument("--execute", dest="dry_run", action="store_false",
                       help="actually run the commands (needs Docker, az/aws CLIs, credentials)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clouddeploy",
        description="Package one containerized app and deploy it to Azure "
                    "Container Apps or AWS ECS.")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    # -c works both before and after the subcommand:
    #   clouddeploy -c config.yaml validate
    #   clouddeploy validate -c config.yaml
    parser.add_argument("-c", "--config", default=argparse.SUPPRESS,
                        help="path to the YAML config file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate the config file")
    p_validate.add_argument("-c", "--config", default=argparse.SUPPRESS,
                            help="path to the YAML config file")

    p_build = sub.add_parser("build", help="build the Docker image (dry-run by default)")
    _add_common(p_build)
    p_build.add_argument("--tag", default=None,
                         help="image tag to build (defaults to app.image from config)")

    p_plan = sub.add_parser("plan", help="print the deployment plan for a provider")
    p_plan.add_argument("-c", "--config", default=argparse.SUPPRESS,
                        help="path to the YAML config file")
    p_plan.add_argument("--provider", required=True, choices=["azure", "aws"])
    p_plan.add_argument("--output-dir", default=None,
                        help="also write the manifests (main.bicep / "
                             "task-definition.json) into this directory")

    p_deploy = sub.add_parser("deploy", help="deploy the app (dry-run by default)")
    _add_common(p_deploy)
    p_deploy.add_argument("--provider", required=True, choices=["azure", "aws"])

    return parser


def _load(args):
    try:
        return load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def cmd_validate(args):
    cfg = _load(args)
    if cfg is None:
        return 2
    print(f"config OK: app '{cfg.app.name}' "
          f"(image {cfg.app.image}, port {cfg.app.port}, "
          f"{cfg.app.cpu} vCPU, {cfg.app.memory})")
    providers = [p for p in ("azure", "aws") if getattr(cfg, p) is not None]
    print(f"providers configured: {', '.join(providers)}")
    return 0


def cmd_build(args):
    cfg = _load(args)
    if cfg is None:
        return 2
    return run_commands([docker_build_command(cfg, tag=args.tag)],
                        dry_run=args.dry_run)


def cmd_plan(args):
    cfg = _load(args)
    if cfg is None:
        return 2
    try:
        cfg.require_provider(args.provider)
        sections = plan(cfg, args.provider)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for title, body in sections:
        print(f"# {title}")
        print(body)
        print()
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        if args.provider == "azure":
            path = os.path.join(args.output_dir, "main.bicep")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(azure_mod.azure_bicep(cfg))
        else:
            path = os.path.join(args.output_dir, "task-definition.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(aws_mod.aws_task_definition_json(cfg))
        print(f"wrote {path}")
    return 0


def cmd_deploy(args):
    cfg = _load(args)
    if cfg is None:
        return 2
    try:
        cfg.require_provider(args.provider)
        commands = deploy_commands(cfg, args.provider)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"# dry-run deploy to {args.provider} (nothing will be executed)")
    else:
        print(f"# deploying to {args.provider} (execute mode)", file=sys.stderr)
    return run_commands(commands, dry_run=args.dry_run)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "config", None):
        parser.error("the following arguments are required: -c/--config")
    handlers = {
        "validate": cmd_validate,
        "build": cmd_build,
        "plan": cmd_plan,
        "deploy": cmd_deploy,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
