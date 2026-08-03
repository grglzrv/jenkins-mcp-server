#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, pattern: str, replacement: str) -> None:
    file_path = ROOT / path
    original = file_path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, original, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"expected one version match in {path}, found {count}")
    file_path.write_text(updated, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: scripts/set_version.py X.Y.Z")
    version = sys.argv[1].strip().removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"invalid semantic version: {version}")

    (ROOT / "VERSION").write_text(version + "\n", encoding="utf-8")
    replace("pyproject.toml", r'^version = "[^"]+"', f'version = "{version}"')
    replace(
        "src/jenkins_mcp_server/__init__.py",
        r'^__version__ = "[^"]+"',
        f'__version__ = "{version}"',
    )
    replace("charts/jenkins-mcp-server/Chart.yaml", r"^version:\s*.*$", f"version: {version}")
    replace(
        "charts/jenkins-mcp-server/Chart.yaml",
        r"^appVersion:\s*.*$",
        f'appVersion: "{version}"',
    )
    replace(
        "deploy/kubernetes/overlays/production/kustomization.yaml",
        r"^\s*newTag:\s*.*$",
        f"    newTag: {version}",
    )
    replace(
        "deploy/kubernetes/base/deployment.yaml",
        r"ghcr\.io/grglzrv/jenkins-mcp-server:[^\s]+",
        f"ghcr.io/grglzrv/jenkins-mcp-server:{version}",
    )
    replace(
        "examples/values/tailscale-production.yaml",
        r'^  tag:\s*"[^"]+"',
        f'  tag: "{version}"',
    )
    replace(
        "examples/argocd/application-oci.yaml",
        r"^    targetRevision:\s*.*$",
        f"    targetRevision: {version}",
    )
    # Added later than the originals and previously missed, so they froze at
    # whatever version was current when they were written.
    replace(
        "examples/argocd/application-minibridge.yaml",
        r"^    targetRevision:\s*.*$",
        f"    targetRevision: {version}",
    )
    replace(
        "examples/argocd/application-hpa-generic.yaml",
        r"^    targetRevision:\s*.*$",
        f"    targetRevision: {version}",
    )
    replace(
        "deploy/kubernetes/minibridge/kustomization.yaml",
        r"^\s*newTag:\s*.*$",
        f"    newTag: {version}-minibridge",
    )
    replace(
        "deploy/kubernetes/minibridge/standalone-deployment.yaml",
        r"ghcr\.io/grglzrv/jenkins-mcp-server:[^\s]+",
        f"ghcr.io/grglzrv/jenkins-mcp-server:{version}-minibridge",
    )
    replace(
        "charts/jenkins-mcp-server/README.md",
        r"^  --version [0-9][^\s]*",
        f"  --version {version}",
    )
    replace(
        "README.md",
        r"^  --version [0-9][^\s]*",
        f"  --version {version}",
    )
    print(f"updated repository version to {version}")


if __name__ == "__main__":
    main()
