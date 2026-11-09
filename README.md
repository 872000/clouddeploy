# clouddeploy

Deploy one containerized app to **Azure Container Apps** or **AWS ECS
(Fargate)** from a single config file.

`clouddeploy` reads a YAML config describing your app (image, port, env vars,
CPU/memory, region, resource names) and generates everything needed to run
it in the cloud: the `docker build` command, the `az` CLI commands plus a
Bicep template for Azure, and the ECS task definition JSON plus the `aws`
CLI commands for AWS.

**Dry-run is the default.** Every command and manifest is printed, never
executed, unless you pass `--execute`. Nothing here talks to Azure or AWS
on its own, and no cloud credentials are ever needed (or stored) to plan a
deployment.

## Requirements

- Python 3.9+
- PyYAML (optional — if it is not installed, clouddeploy falls back to a
  small built-in parser for the YAML subset it needs)
- For real deploys: Docker, and the `az` CLI (Azure) or `aws` CLI (AWS),
  plus your own cloud credentials

## Setup

```bash
git clone https://github.com/872000/clouddeploy.git
cd clouddeploy
pip install -e .          # optional; also installs PyYAML
# or just run it directly with no install:
python -m clouddeploy --help
```

## Configuration

Write a YAML file describing the app and one or both providers
(see [`examples/sample.yaml`](examples/sample.yaml)):

```yaml
app:
  name: hello-cloud
  image: hello-cloud:latest
  port: 8080
  cpu: 0.5            # vCPU: 0.25, 0.5, 1, 2 or 4
  memory: 1Gi         # e.g. 512Mi, 1Gi, 2Gi (must be a valid Fargate combo)
  replicas: 1
  context: sample-app  # docker build context
  env:
    MESSAGE: "Hello from clouddeploy"

azure:
  resource_group: clouddeploy-rg
  location: canadacentral
  environment: clouddeploy-env
  # container_app defaults to app.name when omitted

aws:
  region: ca-central-1
  cluster: clouddeploy-cluster
  service: hello-cloud
  execution_role_arn: arn:aws:iam::123456789012:role/ecsTaskExecutionRole
  subnets:
    - subnet-0123456789abcdef0
  security_groups:
    - sg-0123456789abcdef0
  assign_public_ip: ENABLED
```

Validation rules (all violations are reported together, so you can fix
them in one pass):

- `app.name`, cluster/service names: lowercase letters, digits, hyphens
- `port`: integer 1–65535
- `cpu`: 0.25, 0.5, 1, 2 or 4 vCPU; `memory` must be a valid AWS Fargate
  combination for that CPU (e.g. 0.5 vCPU allows 1–4 GiB) and within the
  Azure Container Apps limits (max 4 vCPU / 8 GiB)
- `env` keys must be valid environment variable names, values must be strings
- AWS region must look like `ca-central-1`; role ARNs, subnet ids
  (`subnet-…`) and security group ids (`sg-…`) are format-checked

## Usage

All commands take `-c / --config`. Dry-run is the default; add
`--execute` only when you really want to run the commands.

```bash
# Validate the config (reports every problem at once)
python -m clouddeploy -c examples/sample.yaml validate

# Show the docker build command (add --execute to actually build)
python -m clouddeploy -c examples/sample.yaml build
python -m clouddeploy -c examples/sample.yaml build --tag hello-cloud:v2

# Print the full deployment plan for a provider
python -m clouddeploy -c examples/sample.yaml plan --provider azure
python -m clouddeploy -c examples/sample.yaml plan --provider aws

# Also write the manifests to a directory (main.bicep / task-definition.json)
python -m clouddeploy -c examples/sample.yaml plan --provider aws --output-dir ./out

# Deploy (dry-run: prints every command, runs nothing)
python -m clouddeploy -c examples/sample.yaml deploy --provider azure
python -m clouddeploy -c examples/sample.yaml deploy --provider aws

# Real deploy: runs the commands for real
python -m clouddeploy -c examples/sample.yaml deploy --provider azure --execute
```

## The sample app

[`sample-app/`](sample-app/) is a tiny stdlib-only Python web service (no
dependencies). It answers JSON on `/` using the `MESSAGE` env var, and
`{"status": "ok"}` on `/healthz`. The sample config points at it, so the
tool works end-to-end in dry-run out of the box, and you can build the
image locally with:

```bash
docker build -t hello-cloud:latest sample-app
docker run -p 8080:8080 hello-cloud:latest
curl localhost:8080/
```

## Real deploy steps

Real deploys need **your own Azure/AWS credentials** and **incur cloud
cost** (Container Apps consumption / Fargate vCPU+memory while the service
runs). clouddeploy never stores credentials.

### Azure Container Apps

1. Sign in and pick a subscription:
   `az login` and `az account set --subscription <id-or-name>`
2. Push the image to a registry the app can pull from, e.g. Azure
   Container Registry:
   ```bash
   az acr create --resource-group clouddeploy-rg --name myregistry --sku Basic
   az acr login --name myregistry
   docker tag hello-cloud:latest myregistry.azurecr.io/hello-cloud:latest
   docker push myregistry.azurecr.io/hello-cloud:latest
   ```
   Then set `app.image` in your config to
   `myregistry.azurecr.io/hello-cloud:latest`.
3. Deploy:
   ```bash
   python -m clouddeploy -c config.yaml deploy --provider azure --execute
   ```
   or generate the Bicep template and deploy it directly:
   ```bash
   python -m clouddeploy -c config.yaml plan --provider azure --output-dir ./out
   az deployment group create --resource-group clouddeploy-rg \
     --template-file ./out/main.bicep
   ```
4. The last command prints the app's public FQDN. Clean up with
   `az group delete --name clouddeploy-rg` when done.

### AWS ECS (Fargate)

1. Configure credentials: `aws configure` (or an IAM role / SSO profile).
   You need an ECS task execution role (e.g. the managed
   `ecsTaskExecutionRole` policy) and a VPC with public subnets and a
   security group allowing inbound traffic on your app port.
2. Deploy:
   ```bash
   python -m clouddeploy -c config.yaml deploy --provider aws --execute
   ```
   This creates the ECR repository, pushes the image, registers the task
   definition, creates the cluster, and creates the Fargate service.
   (The `create-repository` / `create-cluster` steps fail harmlessly if
   those resources already exist.)
3. Find the public IP with
   `aws ecs list-tasks --cluster <cluster> --service-name <service>` then
   `aws ecs describe-tasks`, or check the service in the AWS console.
   Clean up with `aws ecs delete-service`, `aws ecr delete-repository`,
   and `aws ecs delete-cluster` when done.

## Running the tests

Tests are stdlib `unittest`, offline only (no Docker, no network, no cloud
calls; dry-run tests assert that no subprocess is ever started):

```bash
python -m unittest discover -s tests -v
```

## Project layout

```
clouddeploy/
  clouddeploy/
    __init__.py   package version
    cli.py        argparse CLI (validate / build / plan / deploy)
    config.py     YAML loading (PyYAML or built-in fallback) + validation
    deploy.py     build/plan/deploy orchestration, dry-run guard
    azure.py      az CLI commands + Bicep template generation
    aws.py        ECS task definition JSON + aws CLI commands
  sample-app/     tiny stdlib-only web service + Dockerfile
  examples/       sample.yaml — works end-to-end in dry-run
  tests/          unittest suite
```

## License

MIT — see [LICENSE](LICENSE).
