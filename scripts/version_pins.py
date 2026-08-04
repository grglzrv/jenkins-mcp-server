"""Canonical inventory of every repository file pinned to the app version."""

from __future__ import annotations

import re
from dataclasses import dataclass

SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"


@dataclass(frozen=True)
class VersionPin:
    path: str
    pattern: re.Pattern[str]
    expected: int = 1


def pin(path: str, pattern: str, expected: int = 1) -> VersionPin:
    return VersionPin(path, re.compile(pattern, re.MULTILINE), expected)


# Each pattern must expose exactly the application version as a named `version`
# group. Adding a new pinned manifest or documentation command requires adding
# it here; check_version.py also scans the whole repository to catch omissions.
PINS = (
    pin("VERSION", rf"^(?P<version>{SEMVER})$"),
    pin("pyproject.toml", rf'^version = "(?P<version>{SEMVER})"$'),
    pin(
        "src/jenkins_mcp_server/__init__.py",
        rf'^__version__ = "(?P<version>{SEMVER})"$',
    ),
    pin("charts/jenkins-mcp-server/Chart.yaml", rf"^version: (?P<version>{SEMVER})$"),
    pin(
        "charts/jenkins-mcp-server/Chart.yaml",
        rf'^appVersion: "(?P<version>{SEMVER})"$',
    ),
    pin(
        "deploy/kubernetes/base/deployment.yaml",
        rf"ghcr\.io/grglzrv/jenkins-mcp-server:(?P<version>{SEMVER})(?=\s)",
    ),
    pin(
        "deploy/kubernetes/overlays/production/kustomization.yaml",
        rf"^\s*newTag: (?P<version>{SEMVER})$",
    ),
    pin(
        "deploy/kubernetes/minibridge/kustomization.yaml",
        rf"^\s*newTag: (?P<version>{SEMVER})(?=-minibridge$)",
    ),
    pin(
        "deploy/kubernetes/minibridge/standalone-deployment.yaml",
        rf"ghcr\.io/grglzrv/jenkins-mcp-server:(?P<version>{SEMVER})(?=-minibridge)",
    ),
    pin(
        "examples/values/tailscale-production.yaml",
        rf'^\s*tag: "(?P<version>{SEMVER})"$',
    ),
    pin(
        "examples/argocd/application-oci.yaml",
        rf"^\s*targetRevision: (?P<version>{SEMVER})$",
    ),
    pin(
        "examples/argocd/application-minibridge.yaml",
        rf"^\s*targetRevision: (?P<version>{SEMVER})$",
    ),
    pin(
        "examples/argocd/application-hpa-generic.yaml",
        rf"^\s*targetRevision: (?P<version>{SEMVER})$",
    ),
    pin(
        "compose.yaml",
        rf"\$\{{JENKINS_MCP_VERSION:-(?P<version>{SEMVER})\}}",
        expected=2,
    ),
    pin(
        "README.md",
        rf"^\s*--version (?P<version>{SEMVER})(?=\s|$)",
    ),
    pin("README.md", rf"^NEW_VERSION=(?P<version>{SEMVER})$"),
    pin(
        "charts/jenkins-mcp-server/README.md",
        rf"^\s*--version (?P<version>{SEMVER})(?=\s|$)",
        expected=2,
    ),
    pin(
        "charts/jenkins-mcp-server/README.md",
        rf"^NEW_VERSION=(?P<version>{SEMVER})$",
    ),
    pin("CONTRIBUTING.md", rf"^NEW_VERSION=(?P<version>{SEMVER})$"),
    pin(
        "docs/releasing/RELEASE.md",
        rf"^NEW_VERSION=(?P<version>{SEMVER})$",
    ),
)


def replace_match_version(match: re.Match[str], version: str) -> str:
    """Replace only the named version group and retain its surrounding syntax."""
    whole = match.group(0)
    start, end = match.span("version")
    offset = match.start(0)
    return whole[: start - offset] + version + whole[end - offset :]
