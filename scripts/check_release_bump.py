#!/usr/bin/env python3
"""Require a new release version when deployable behavior changes."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)

# This is an explicit policy, not a broad directory guess. Documentation,
# tests, integration fixtures, and CI/release machinery do not change a
# deployable runtime. Chart README files are also documentation even though
# Helm keeps them beside the packaged templates.
EXACT_RELEASE_PATHS = {
    ".dockerignore",
    "Dockerfile",
    "compose.yaml",
    "pyproject.toml",
}
RELEASE_PREFIXES = (
    "src/",
    "charts/jenkins-mcp-server/",
    "docker/",
    "deploy/",
    "examples/argocd/",
    "examples/values/",
)
EXACT_EXEMPT_PATHS = {
    "docker/policy_test.rego",
}


class ReleaseBumpError(ValueError):
    """The diff changes a release artifact without a valid version increase."""


def _git(root: Path, *args: str) -> bytes:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseBumpError(f"git {' '.join(args)} failed: {detail}")
    return process.stdout


def is_release_path(path: str) -> bool:
    """Return whether a repository path changes deployable behavior."""
    if path in EXACT_EXEMPT_PATHS or path.endswith(".md"):
        return False
    if path in EXACT_RELEASE_PATHS:
        return True
    return path.startswith(RELEASE_PREFIXES)


def changed_paths(base: str, root: Path = ROOT) -> list[str]:
    """Return both sides of renames so moving runtime code cannot evade policy."""
    _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    output = _git(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        "-z",
        base,
        "--",
    )
    return sorted({item.decode("utf-8") for item in output.split(b"\0") if item})


def parse_semver(value: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    match = SEMVER.fullmatch(value)
    if not match:
        raise ReleaseBumpError(f"invalid semantic version: {value!r}")
    prerelease = match.group("prerelease")
    identifiers = tuple(prerelease.split(".")) if prerelease else None
    if identifiers and any(
        not identifier
        or (identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0")
        for identifier in identifiers
    ):
        raise ReleaseBumpError(f"invalid semantic version: {value!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        identifiers,
    )


def compare_semver(left: str, right: str) -> int:
    """Compare SemVer precedence, including numeric prerelease identifiers."""
    left_major, left_minor, left_patch, left_pre = parse_semver(left)
    right_major, right_minor, right_patch, right_pre = parse_semver(right)
    left_core = (left_major, left_minor, left_patch)
    right_core = (right_major, right_minor, right_patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_id, right_id in zip(left_pre, right_pre, strict=False):
        if left_id == right_id:
            continue
        left_numeric = left_id.isdigit()
        right_numeric = right_id.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_id) > int(right_id) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_id > right_id else -1
    if len(left_pre) == len(right_pre):
        return 0
    return 1 if len(left_pre) > len(right_pre) else -1


def require_newer_version(candidate: str, baseline: str) -> None:
    if compare_semver(candidate, baseline) <= 0:
        raise ReleaseBumpError(
            f"release version {candidate} must be newer than {baseline}"
        )


def check_release_bump(base: str, root: Path = ROOT) -> tuple[str, str, list[str]]:
    paths = changed_paths(base, root)
    release_paths = [path for path in paths if is_release_path(path)]
    if not release_paths:
        return "", "", []

    try:
        base_version = _git(root, "show", f"{base}:VERSION").decode("utf-8").strip()
    except ReleaseBumpError as exc:
        raise ReleaseBumpError("the base commit has no readable VERSION") from exc
    version_file = root / "VERSION"
    if not version_file.is_file():
        raise ReleaseBumpError("HEAD has no VERSION file")
    head_version = version_file.read_text(encoding="utf-8").strip()
    # VERSION must change, not necessarily increase. Ordering is enforced at
    # publish time by --assert-newer against the latest published release, which
    # is the value that actually matters: a version already on the registry must
    # never be republished. Requiring a local increase additionally forbids
    # withdrawing a version that was tagged but pulled, which is a legitimate
    # correction and was not expressible before.
    if head_version == base_version:
        files = "\n".join(f"  {path}" for path in release_paths)
        raise ReleaseBumpError(
            "these files change deployable behavior:\n"
            f"{files}\n\n"
            f"VERSION must change: it is {head_version} on both sides\n"
            "Run: make version VERSION=X.Y.Z"
        )
    return base_version, head_version, release_paths


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 3 and args[0] == "--assert-newer":
        try:
            require_newer_version(args[1], args[2])
        except ReleaseBumpError as exc:
            print(f"release order check failed: {exc}", file=sys.stderr)
            return 1
        print(f"release version {args[1]} is newer than {args[2]}")
        return 0
    if len(args) != 1:
        print(
            "usage: check_release_bump.py BASE_COMMIT\n"
            "       check_release_bump.py --assert-newer CANDIDATE BASELINE",
            file=sys.stderr,
        )
        return 2
    try:
        base_version, head_version, paths = check_release_bump(args[0])
    except ReleaseBumpError as exc:
        print(f"release bump check failed: {exc}", file=sys.stderr)
        return 1
    if paths:
        print(
            f"release-impacting changes are covered by VERSION "
            f"{base_version} -> {head_version}"
        )
    else:
        print("no release-impacting changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
