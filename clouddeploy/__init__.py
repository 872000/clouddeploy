"""clouddeploy: package one containerized app and deploy it to Azure
Container Apps or AWS ECS from a single config file.

Everything is dry-run by default: commands and manifests are printed, never
executed, unless the user passes ``--execute``.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
