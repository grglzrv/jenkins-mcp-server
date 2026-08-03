#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"version check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def scan_for_stale_pins(version: str) -> list[str]:
    """Catch version pins in files nobody remembered to add to set_version.py.

    Several examples froze at the version current when they were written,
    because they were added after the release script and never wired into it.
    """
    import re

    stale: list[str] = []
    patterns = [
        ("ghcr.io/grglzrv/jenkins-mcp-server:", re.compile(r"jenkins-mcp-server:(\d+\.\d+\.\d+)")),
        ("targetRevision:", re.compile(r"^\s*targetRevision:\s*(\d+\.\d+\.\d+)", re.M)),
        ("newTag:", re.compile(r"^\s*newTag:\s*(\d+\.\d+\.\d+)", re.M)),
        ("--version", re.compile(r"--version\s+(\d+\.\d+\.\d+)")),
    ]
    roots = ["deploy", "examples", "charts"]
    candidates = [ROOT / "README.md"]
    for root in roots:
        candidates.extend(sorted((ROOT / root).rglob("*")))
    for path in candidates:
        if True:
            if path.suffix not in {".yaml", ".yml", ".md"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for _label, pattern in patterns:
                for found in pattern.findall(text):
                    if found != version:
                        stale.append(f"{path.relative_to(ROOT)}: pins {found}")
    return sorted(set(stale))


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        fail(f"VERSION is not semantic: {version!r}")

    with (ROOT / "pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)
    python_version = pyproject["project"]["version"]

    init_text = (ROOT / "src/jenkins_mcp_server/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    if not match:
        fail("could not find __version__")
    package_version = match.group(1)

    chart_text = (ROOT / "charts/jenkins-mcp-server/Chart.yaml").read_text(encoding="utf-8")
    chart_version_match = re.search(r"^version:\s*([^\s]+)", chart_text, re.MULTILINE)
    app_version_match = re.search(r'^appVersion:\s*["\']?([^"\'\s]+)', chart_text, re.MULTILINE)
    if not chart_version_match or not app_version_match:
        fail("could not read chart version/appVersion")

    raw_kustomize = (
        ROOT / "deploy/kubernetes/overlays/production/kustomization.yaml"
    ).read_text(encoding="utf-8")
    raw_tag_match = re.search(r"^\s*newTag:\s*([^\s]+)", raw_kustomize, re.MULTILINE)
    if not raw_tag_match:
        fail("could not read Kustomize image tag")

    raw_deployment = (ROOT / "deploy/kubernetes/base/deployment.yaml").read_text(encoding="utf-8")
    raw_image_match = re.search(r"ghcr\.io/grglzrv/jenkins-mcp-server:([^\s]+)", raw_deployment)
    if not raw_image_match:
        fail("could not read raw Deployment image tag")

    example_values = (
        ROOT / "examples/values/tailscale-production.yaml"
    ).read_text(encoding="utf-8")
    example_tag_match = re.search(r'^\s*tag:\s*["\']?([^"\'\s]+)', example_values, re.MULTILINE)
    if not example_tag_match:
        fail("could not read Helm example image tag")

    argo_oci = (ROOT / "examples/argocd/application-oci.yaml").read_text(encoding="utf-8")
    argo_revision_match = re.search(r"^\s*targetRevision:\s*([^\s]+)", argo_oci, re.MULTILINE)
    if not argo_revision_match:
        fail("could not read Argo CD OCI chart version")

    actual = {
        "VERSION": version,
        "pyproject": python_version,
        "package": package_version,
        "chart": chart_version_match.group(1),
        "appVersion": app_version_match.group(1),
        "kustomize": raw_tag_match.group(1),
        "rawDeployment": raw_image_match.group(1),
        "helmExample": example_tag_match.group(1),
        "argoOCI": argo_revision_match.group(1),
    }
    mismatches = {name: value for name, value in actual.items() if value != version}
    if mismatches:
        fail(f"expected {version}; mismatches: {mismatches}")

    stale = scan_for_stale_pins(version)
    if stale:
        fail("stale version pins found:\n  " + "\n  ".join(stale))

    print(f"all versions are synchronized at {version}")


if __name__ == "__main__":
    main()
