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


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: scripts/set_version.py X.Y.Z")
    version = sys.argv[1].strip().removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"invalid semantic version: {version}")

    for pin in PINS:
        replace_pin(pin, version)
    print(f"updated repository version to {version}")


if __name__ == "__main__":
    main()
