#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

from version_pins import PINS, SEMVER

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "pytest-of-root",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
}
EXCLUDED_FILES = {"CHANGELOG.md"}  # Historical releases must retain their versions.

# These contexts represent deployable application/chart pins, not unrelated
# dependency, Jenkins, Kubernetes, or historical changelog versions. Scanning
# the whole repository means a new manifest or README cannot silently escape
# the explicit PINS inventory above.
IMAGE_PIN_PATTERN = re.compile(
    rf"ghcr\.io/grglzrv/jenkins-mcp-server:(?P<version>{SEMVER})"
)
REPOSITORY_PIN_PATTERNS = (
    IMAGE_PIN_PATTERN,
    re.compile(rf"^\s*targetRevision:\s*(?P<version>{SEMVER})", re.MULTILINE),
    re.compile(rf"^\s*newTag:\s*(?P<version>{SEMVER})", re.MULTILINE),
    re.compile(rf"--version\s+(?P<version>{SEMVER})"),
    re.compile(rf"^NEW_VERSION=(?P<version>{SEMVER})$", re.MULTILINE),
    re.compile(rf"JENKINS_MCP_VERSION:-(?P<version>{SEMVER})"),
    re.compile(
        rf'^\s*(?:appVersion|version|tag):\s*["\']?(?P<version>{SEMVER})',
        re.MULTILINE,
    ),
    re.compile(rf'^__version__\s*=\s*["\'](?P<version>{SEMVER})', re.MULTILINE),
)


def fail(message: str) -> None:
    print(f"version check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def repository_text_files(root: Path):
    """Yield every small UTF-8 repository file, regardless of extension."""
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.name in EXCLUDED_FILES
            or any(part in EXCLUDED_PARTS for part in path.parts)
        ):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield path


def managed_pin_errors(version: str, root: Path = ROOT) -> list[str]:
    """Validate the exact count and value of every intentionally managed pin."""
    errors: list[str] = []
    for pin in PINS:
        path = root / pin.path
        if not path.is_file():
            errors.append(f"{pin.path}: managed pin file is missing")
            continue
        matches = list(pin.pattern.finditer(path.read_text(encoding="utf-8")))
        if len(matches) != pin.expected:
            errors.append(
                f"{pin.path}: expected {pin.expected} managed pin(s) for "
                f"{pin.pattern.pattern!r}, found {len(matches)}"
            )
            continue
        found = {match.group("version") for match in matches}
        if found != {version}:
            errors.append(f"{pin.path}: expected {version}, found {sorted(found)}")
    return errors


def scan_for_stale_pins(version: str, root: Path = ROOT) -> list[str]:
    """Find stale application pins anywhere, including files not yet inventoried."""
    stale: list[str] = []
    for path in repository_text_files(root):
        text = path.read_text(encoding="utf-8")
        for pattern in REPOSITORY_PIN_PATTERNS:
            for match in pattern.finditer(text):
                found = match.group("version")
                if found.endswith("-minibridge"):
                    found = found.removesuffix("-minibridge")
                if found != version:
                    stale.append(f"{path.relative_to(root)}: pins {found}")
    return sorted(set(stale))


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(SEMVER, version):
        fail(f"VERSION is not semantic: {version!r}")

    errors = managed_pin_errors(version)
    errors.extend(scan_for_stale_pins(version))
    if errors:
        fail("repository version pins are inconsistent:\n  " + "\n  ".join(errors))

    count = sum(pin.expected for pin in PINS)
    files = len({pin.path for pin in PINS})
    print(
        f"all {count} managed version pins in {files} files are synchronized "
        f"at {version}; repository-wide stale-pin scan passed"
    )


if __name__ == "__main__":
    main()
