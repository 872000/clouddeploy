"""Configuration loading and validation for clouddeploy.

The config file is YAML. PyYAML is used when it is installed; otherwise a
small built-in parser handles the subset of YAML this tool needs (nested
mappings, lists, quoted/plain scalars, inline lists). No other third-party
dependency is required.

Example config (see ``examples/sample.yaml``)::

    app:
      name: hello-cloud
      image: hello-cloud:latest
      port: 8080
      cpu: 0.5
      memory: 1Gi
      env:
        MESSAGE: "Hello from clouddeploy"
      replicas: 1
    azure:
      resource_group: clouddeploy-rg
      location: canadacentral
      environment: clouddeploy-env
    aws:
      region: ca-central-1
      cluster: clouddeploy-cluster
      service: hello-cloud
      execution_role_arn: arn:aws:iam::123456789012:role/ecsTaskExecutionRole
      subnets:
        - subnet-0123456789abcdef0
      security_groups:
        - sg-0123456789abcdef0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is installed in dev/CI
    yaml = None


class ConfigError(ValueError):
    """Raised when a config file is missing, malformed, or invalid."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_ARN_ROLE_RE = re.compile(r"^arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_-]+$")
_SUBNET_RE = re.compile(r"^subnet-[0-9a-f]+$")
_SG_RE = re.compile(r"^sg-[0-9a-f]+$")
_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(Mi|Gi)$")

# Valid AWS Fargate CPU/memory combinations (vCPU -> allowed memory in MiB).
# Source: AWS docs, "Task size" table for Fargate.
FARGATE_MEMORY_MIB = {
    0.25: [512, 1024, 2048],
    0.5: [1024, 2048, 3072, 4096],
    1.0: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
    2.0: [4096, 5120, 6144, 7168, 8192, 9216, 10240, 11264, 12288,
          13312, 14336, 15360, 16384],
    4.0: list(range(8192, 30721, 1024)),
}

# Azure Container Apps (consumption) allows 0.25-4 vCPU and 0.5-8 GiB memory.
AZURE_MAX_CPU = 4.0
AZURE_MAX_MEMORY_MIB = 8192

_TOP_LEVEL_KEYS = {"app", "azure", "aws"}


def parse_memory(value, field_name, errors):
    """Parse a memory size (``"512Mi"``, ``"1Gi"``, ``"1.5Gi"`` or an int in
    MiB) and return MiB as an int, or None (recording an error)."""
    if isinstance(value, bool):
        errors.append(f"{field_name}: must be like '512Mi' or '1Gi', not a boolean")
        return None
    if isinstance(value, int):
        if value <= 0:
            errors.append(f"{field_name}: must be greater than 0")
            return None
        return value
    if isinstance(value, float):
        errors.append(f"{field_name}: use a string like '1Gi', not a bare number")
        return None
    if isinstance(value, str):
        match = _MEMORY_RE.match(value.strip())
        if not match:
            errors.append(
                f"{field_name}: {value!r} is not a valid memory size "
                "(use e.g. '512Mi' or '1Gi')"
            )
            return None
        amount = float(match.group(1))
        mib = int(amount * 1024) if match.group(2) == "Gi" else int(amount)
        if mib <= 0:
            errors.append(f"{field_name}: must be greater than 0")
            return None
        return mib
    errors.append(f"{field_name}: must be a string like '512Mi' or '1Gi'")
    return None


def _require(mapping, key, field_name, errors, expected):
    """Fetch a required key, recording an error if it is missing."""
    if key not in mapping or mapping[key] is None:
        errors.append(f"{field_name}.{key}: missing required value ({expected})")
        return None
    return mapping[key]


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    name: str
    image: str
    port: int
    cpu: float
    memory_mib: int
    env: dict = field(default_factory=dict)
    replicas: int = 1
    context: str = "."

    @property
    def memory(self):
        """Memory as a Container Apps style string, e.g. ``'1Gi'``."""
        if self.memory_mib % 1024 == 0:
            return f"{self.memory_mib // 1024}Gi"
        return f"{self.memory_mib}Mi"

    @property
    def fargate_cpu_units(self):
        """CPU in ECS task-definition units (1 vCPU = 1024 units)."""
        return str(int(self.cpu * 1024))

    @property
    def fargate_memory(self):
        """Memory in MiB as a string, as ECS task definitions expect."""
        return str(self.memory_mib)


@dataclass
class AzureConfig:
    resource_group: str
    location: str
    environment: str
    container_app: str | None = None  # defaults to app.name

    def app_name(self, app_name):
        return self.container_app or app_name


@dataclass
class AwsConfig:
    region: str
    cluster: str
    service: str
    execution_role_arn: str
    subnets: list
    security_groups: list
    task_role_arn: str | None = None
    assign_public_ip: str = "ENABLED"

    @property
    def account_id(self):
        """AWS account id parsed out of the execution role ARN."""
        return self.execution_role_arn.split(":")[4]


@dataclass
class DeployConfig:
    app: AppConfig
    azure: AzureConfig | None = None
    aws: AwsConfig | None = None

    def require_provider(self, provider):
        """Return the provider config, or raise if it is not configured."""
        section = getattr(self, provider)
        if section is None:
            raise ConfigError(
                f"config has no '{provider}' section, but provider "
                f"'{provider}' was requested"
            )
        return section


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_app(raw, errors):
    if not isinstance(raw, dict):
        errors.append("app: must be a mapping")
        return None
    name = _require(raw, "name", "app", errors, "lowercase letters, digits, hyphens")
    image = _require(raw, "image", "app", errors, "e.g. 'hello-cloud:latest'")
    port = _require(raw, "port", "app", errors, "integer 1-65535")
    cpu = _require(raw, "cpu", "app", errors, "number, e.g. 0.5")
    memory = _require(raw, "memory", "app", errors, "e.g. '1Gi'")

    if isinstance(name, str) and not _NAME_RE.match(name):
        errors.append(
            f"app.name: {name!r} is invalid (lowercase letters, digits and "
            "hyphens, must start and end with a letter or digit, max 63 chars)"
        )
    if image is not None and (not isinstance(image, str) or not image.strip()):
        errors.append("app.image: must be a non-empty string")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int)
                             or not 1 <= port <= 65535):
        errors.append(f"app.port: {port!r} is invalid (integer 1-65535)")
    cpu_value = None
    if cpu is not None:
        if isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or cpu <= 0:
            errors.append(f"app.cpu: {cpu!r} is invalid (number greater than 0)")
        else:
            cpu_value = float(cpu)
            if cpu_value not in FARGATE_MEMORY_MIB:
                errors.append(
                    f"app.cpu: {cpu_value} is not a supported size "
                    "(use 0.25, 0.5, 1, 2 or 4 vCPU)"
                )
    memory_mib = None
    if memory is not None:
        memory_mib = parse_memory(memory, "app.memory", errors)

    env = raw.get("env") or {}
    if not isinstance(env, dict):
        errors.append("app.env: must be a mapping of NAME: value")
        env = {}
    else:
        for key, value in env.items():
            if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
                errors.append(f"app.env: {key!r} is not a valid environment variable name")
            if not isinstance(value, str):
                errors.append(f"app.env.{key}: value must be a string, got {value!r}")

    replicas = raw.get("replicas", 1)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
        errors.append(f"app.replicas: {replicas!r} is invalid (integer, at least 1)")
        replicas = 1
    elif replicas > 30:
        errors.append("app.replicas: must be 30 or fewer")
        replicas = 1

    context = raw.get("context", ".")
    if not isinstance(context, str) or not context.strip():
        errors.append("app.context: must be a non-empty string (docker build context path)")
        context = "."

    for key in raw:
        if key not in {"name", "image", "port", "cpu", "memory", "env",
                       "replicas", "context"}:
            errors.append(f"app: unknown key {key!r}")

    if errors:
        return None
    return AppConfig(name=name, image=image, port=port, cpu=cpu_value,
                     memory_mib=memory_mib, env=dict(env),
                     replicas=replicas, context=context)


def _validate_azure(raw, errors):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append("azure: must be a mapping")
        return None
    resource_group = _require(raw, "resource_group", "azure", errors, "e.g. 'my-rg'")
    location = _require(raw, "location", "azure", errors, "e.g. 'canadacentral'")
    environment = _require(raw, "environment", "azure", errors, "e.g. 'my-env'")
    container_app = raw.get("container_app")
    for key, value in (("resource_group", resource_group),
                       ("location", location), ("environment", environment)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"azure.{key}: must be a non-empty string")
    if container_app is not None:
        if not isinstance(container_app, str) or not _NAME_RE.match(container_app):
            errors.append(f"azure.container_app: {container_app!r} is not a valid app name")
    for key in raw:
        if key not in {"resource_group", "location", "environment", "container_app"}:
            errors.append(f"azure: unknown key {key!r}")
    if errors:
        return None
    return AzureConfig(resource_group=resource_group, location=location,
                       environment=environment, container_app=container_app)


def _validate_aws(raw, errors):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append("aws: must be a mapping")
        return None
    region = _require(raw, "region", "aws", errors, "e.g. 'ca-central-1'")
    cluster = _require(raw, "cluster", "aws", errors, "e.g. 'my-cluster'")
    service = _require(raw, "service", "aws", errors, "e.g. 'my-service'")
    role = _require(raw, "execution_role_arn", "aws", errors,
                    "ARN of an ecsTaskExecutionRole")
    subnets = _require(raw, "subnets", "aws", errors, "list of subnet ids")
    sgs = _require(raw, "security_groups", "aws", errors, "list of security group ids")

    if isinstance(region, str) and not _REGION_RE.match(region):
        errors.append(f"aws.region: {region!r} is not a valid AWS region (e.g. 'ca-central-1')")
    if isinstance(cluster, str) and not _NAME_RE.match(cluster):
        errors.append(f"aws.cluster: {cluster!r} is not a valid name")
    if isinstance(service, str) and not _NAME_RE.match(service):
        errors.append(f"aws.service: {service!r} is not a valid name")
    if isinstance(role, str) and not _ARN_ROLE_RE.match(role):
        errors.append(
            f"aws.execution_role_arn: {role!r} is not a valid IAM role ARN "
            "(e.g. 'arn:aws:iam::123456789012:role/ecsTaskExecutionRole')"
        )
    task_role = raw.get("task_role_arn")
    if task_role is not None and not (isinstance(task_role, str)
                                      and _ARN_ROLE_RE.match(task_role)):
        errors.append(f"aws.task_role_arn: {task_role!r} is not a valid IAM role ARN")
    if subnets is not None:
        if not isinstance(subnets, list) or not subnets:
            errors.append("aws.subnets: must be a non-empty list of subnet ids")
        else:
            for subnet in subnets:
                if not isinstance(subnet, str) or not _SUBNET_RE.match(subnet):
                    errors.append(f"aws.subnets: {subnet!r} is not a valid subnet id")
    if sgs is not None:
        if not isinstance(sgs, list) or not sgs:
            errors.append("aws.security_groups: must be a non-empty list of security group ids")
        else:
            for sg in sgs:
                if not isinstance(sg, str) or not _SG_RE.match(sg):
                    errors.append(f"aws.security_groups: {sg!r} is not a valid security group id")
    assign_public_ip = raw.get("assign_public_ip", "ENABLED")
    if assign_public_ip not in ("ENABLED", "DISABLED"):
        errors.append("aws.assign_public_ip: must be 'ENABLED' or 'DISABLED'")
    for key in raw:
        if key not in {"region", "cluster", "service", "execution_role_arn",
                       "task_role_arn", "subnets", "security_groups",
                       "assign_public_ip"}:
            errors.append(f"aws: unknown key {key!r}")
    if errors:
        return None
    return AwsConfig(region=region, cluster=cluster, service=service,
                     execution_role_arn=role, subnets=list(subnets),
                     security_groups=list(sgs), task_role_arn=task_role,
                     assign_public_ip=assign_public_ip)


def validate_config(raw):
    """Validate a parsed config mapping and return a DeployConfig.

    Raises ConfigError listing every problem found, so the user can fix
    them all in one pass.
    """
    errors = []
    if not isinstance(raw, dict):
        raise ConfigError("config: top level must be a mapping with an 'app' section")
    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            errors.append(f"config: unknown top-level key {key!r} (expected 'app', 'azure', 'aws')")
    if "app" not in raw:
        errors.append("config: missing required 'app' section")
    if "azure" not in raw and "aws" not in raw:
        errors.append("config: at least one of 'azure' or 'aws' must be configured")

    app = _validate_app(raw.get("app"), errors) if "app" in raw else None
    azure = _validate_azure(raw.get("azure"), errors)
    aws = _validate_aws(raw.get("aws"), errors)

    if app is not None and aws is not None:
        allowed = FARGATE_MEMORY_MIB.get(app.cpu)
        if allowed is not None and app.memory_mib not in allowed:
            errors.append(
                f"app.memory: {app.memory} is not valid with cpu {app.cpu} on "
                f"AWS Fargate (allowed: {', '.join(_fmt_mib(m) for m in allowed)})"
            )
    if app is not None:
        if app.cpu > AZURE_MAX_CPU:
            errors.append(f"app.cpu: {app.cpu} exceeds the Azure Container Apps maximum of 4")
        if app.memory_mib > AZURE_MAX_MEMORY_MIB:
            errors.append(
                f"app.memory: {app.memory} exceeds the Azure Container Apps "
                "maximum of 8Gi"
            )

    if errors:
        raise ConfigError("invalid config:\n" + "\n".join(f"  - {e}" for e in errors))
    return DeployConfig(app=app, azure=azure, aws=aws)


def _fmt_mib(mib):
    return f"{mib // 1024}Gi" if mib % 1024 == 0 else f"{mib}Mi"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_config(path):
    """Load and validate the config file at *path*."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path!r}: {exc.strerror}") from exc
    raw = _parse_yaml(text, path)
    return validate_config(raw)


def _parse_yaml(text, path):
    if yaml is not None:
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: YAML parse error: {exc}") from exc
        if raw is None:
            raise ConfigError(f"{path}: config file is empty")
        return raw
    return _parse_simple_yaml(text, path)


# ---------------------------------------------------------------------------
# Minimal YAML subset parser (used only when PyYAML is not installed)
#
# Supports nested mappings, lists (including "- key: value" items), quoted
# and plain scalars, inline lists like [a, b], booleans, null, and comments.
# Tabs for indentation are rejected, as in real YAML.
# ---------------------------------------------------------------------------

def _strip_comment(line):
    in_single = in_double = False
    for i, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                return line[:i].rstrip()
    return line.rstrip()


def _scalar(text):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(item) for item in _split_flow(inner)]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "True", "yes", "Yes"):
        return True
    if text in ("false", "False", "no", "No"):
        return False
    if text in ("null", "Null", "~", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_flow(text):
    parts, current, in_single, in_double = [], "", False, False
    for char in text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == "," and not in_single and not in_double:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _parse_simple_yaml(text, path):
    lines = []
    for raw_line in text.splitlines():
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ConfigError(f"{path}: tabs are not allowed for indentation")
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip()))
    if not lines:
        raise ConfigError(f"{path}: config file is empty")
    value, pos = _parse_block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise ConfigError(f"{path}: unexpected content at line: {lines[pos][1]!r}")
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: top level must be a mapping with an 'app' section")
    return value


def _parse_block(lines, pos, indent):
    if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
        return _parse_list(lines, pos, indent)
    return _parse_mapping(lines, pos, indent)


def _parse_mapping(lines, pos, indent):
    result = {}
    while pos < len(lines):
        line_indent, content = lines[pos]
        if line_indent < indent or content.startswith("- "):
            break
        if line_indent > indent:
            raise ConfigError(f"unexpected indentation: {content!r}")
        if ":" not in content:
            raise ConfigError(f"expected 'key: value', got {content!r}")
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        pos += 1
        if rest:
            result[key] = _scalar(rest)
        elif pos < len(lines) and lines[pos][0] > indent:
            value, pos = _parse_block(lines, pos, lines[pos][0])
            result[key] = value
        else:
            result[key] = None
    return result, pos


def _parse_list(lines, pos, indent):
    result = []
    while pos < len(lines):
        line_indent, content = lines[pos]
        if line_indent != indent or not (content.startswith("- ") or content == "-"):
            break
        item = content[1:].strip()
        pos += 1
        if not item:
            if pos < len(lines) and lines[pos][0] > indent:
                value, pos = _parse_block(lines, pos, lines[pos][0])
                result.append(value)
            else:
                result.append(None)
        elif ":" in item and not item.startswith(("[", '"', "'")):
            # "- key: value" starts an inline mapping; continuation lines
            # are indented two spaces past the dash.
            sub_lines = [(indent + 2, item)]
            while pos < len(lines) and lines[pos][0] > indent:
                sub_lines.append(lines[pos])
                pos += 1
            value, _ = _parse_mapping(sub_lines, 0, indent + 2)
            result.append(value)
        else:
            result.append(_scalar(item))
    return result, pos
