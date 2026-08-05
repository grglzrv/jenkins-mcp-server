#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

from version_pins import PINS, replace_match_version

ROOT = Path(__file__).resolve().parents[1]


def replace_pin(pin, version: str) -> None:
    """Rewrite one declared application-version pin."""
    file_path = ROOT / pin.path
    original = file_path.read_text(encoding="utf-8")
    updated, count = pin.pattern.subn(
        lambda match: replace_match_version(match, version), original
    )
    if count != pin.expected:
        raise RuntimeError(
            f"expected {pin.expected} version match(es) for {pin.pattern.pattern!r} "
            f"in {pin.path}, found {count}"
        )
    file_path.write_text(updated, encoding="utf-8")


def _require_release_notes(version: str) -> None:
    """Refuse to bump without release notes for the new version.

    The release workflow triggers on a VERSION change and validates the
    changelog first, so bumping without notes produces a failed release that no
    later commit re-triggers: the version has already changed. `make version`
    prepares the notes before calling this script, so this only fires when the
    script is invoked directly.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from changelog import ChangelogError, validate_document  # noqa: PLC0415

    changelog = ROOT / "CHANGELOG.md"
    try:
        validate_document(changelog.read_text(encoding="utf-8"), version)
    except ChangelogError as exc:
        raise SystemExit(
            f"refusing to bump to {version}: {exc}\n"
            "\n"
            "Release notes must exist before the version changes, or the release\n"
            "fails and cannot be retriggered without another bump.\n"
            "\n"
            "Fill in the Unreleased section of CHANGELOG.md, then run:\n"
            f"  make version VERSION={version}\n"
            "which prepares the notes and rewrites every version pin together."
        ) from exc


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: scripts/set_version.py X.Y.Z")
    version = sys.argv[1].strip().removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"invalid semantic version: {version}")

    _require_release_notes(version)

    for pin in PINS:
        replace_pin(pin, version)
    print(f"updated repository version to {version}")


if __name__ == "__main__":
    main()
