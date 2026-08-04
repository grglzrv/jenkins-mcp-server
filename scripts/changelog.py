#!/usr/bin/env python3
"""Prepare, validate, and render professional release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
RELEASE_HEADER = re.compile(
    r"^## \[(?P<version>[^]]+)] - (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})$"
)
HEADINGS = (
    "Highlights",
    "New Features",
    "Improvements",
    "Bug Fixes",
    "Breaking Changes",
    "Known Issues",
    "Security",
    "Upgrade Notes",
)
UNRELEASED_TEMPLATE = "\n\n".join(
    f"### {heading}\n\n- None yet." for heading in HEADINGS
)
INCOMPLETE_MARKERS = ("none yet", "tbd", "todo", "coming soon")


class ChangelogError(ValueError):
    """Raised when the changelog does not satisfy the release contract."""


def _section_bounds(lines: list[str], start: int) -> tuple[int, int]:
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return start, end


def _find_unreleased(lines: list[str]) -> tuple[int, int]:
    matches = [index for index, line in enumerate(lines) if line == "## [Unreleased]"]
    if len(matches) != 1:
        raise ChangelogError(
            f"expected exactly one '## [Unreleased]' section, found {len(matches)}"
        )
    return _section_bounds(lines, matches[0])


def _find_release(lines: list[str], version: str) -> tuple[int, int, date]:
    matches: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = RELEASE_HEADER.fullmatch(line)
        if match and match.group("version") == version:
            matches.append((index, match))
    if len(matches) != 1:
        raise ChangelogError(
            f"expected exactly one release entry for {version}, found {len(matches)}"
        )
    start, match = matches[0]
    try:
        released = date.fromisoformat(match.group("date"))
    except ValueError as exc:
        raise ChangelogError(f"release {version} has an invalid ISO date") from exc
    return (*_section_bounds(lines, start), released)


def _category_bodies(lines: list[str], start: int, end: int) -> dict[str, list[str]]:
    headings: list[tuple[int, str]] = []
    for index in range(start + 1, end):
        if lines[index].startswith("### "):
            headings.append((index, lines[index].removeprefix("### ")))

    names = [name for _, name in headings]
    missing = [name for name in HEADINGS if name not in names]
    unexpected = [name for name in names if name not in HEADINGS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    errors: list[str] = []
    if missing:
        errors.append("missing categories: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected categories: " + ", ".join(unexpected))
    if duplicates:
        errors.append("duplicate categories: " + ", ".join(duplicates))
    if names and names != list(HEADINGS):
        errors.append("categories are not in the required order")
    if errors:
        raise ChangelogError("; ".join(errors))

    bodies: dict[str, list[str]] = {}
    for position, (heading_index, name) in enumerate(headings):
        body_end = headings[position + 1][0] if position + 1 < len(headings) else end
        body = [line for line in lines[heading_index + 1 : body_end] if line.strip()]
        if not body:
            raise ChangelogError(f"category '{name}' is empty")
        if not any(line.startswith("- ") for line in body):
            raise ChangelogError(f"category '{name}' must contain at least one bullet")
        bodies[name] = body
    return bodies


def _check_complete_release(bodies: dict[str, list[str]], version: str) -> None:
    for heading, body in bodies.items():
        normalized = " ".join(body).lower()
        marker = next((value for value in INCOMPLETE_MARKERS if value in normalized), None)
        if marker:
            raise ChangelogError(
                f"release {version} category '{heading}' still contains placeholder {marker!r}"
            )


def validate_document(text: str, version: str) -> None:
    """Validate the Unreleased template and one complete release entry."""
    if not SEMVER.fullmatch(version):
        raise ChangelogError(f"invalid semantic version: {version!r}")
    lines = text.splitlines()
    unreleased_start, unreleased_end = _find_unreleased(lines)
    _category_bodies(lines, unreleased_start, unreleased_end)
    release_start, release_end, _ = _find_release(lines, version)
    bodies = _category_bodies(lines, release_start, release_end)
    _check_complete_release(bodies, version)


def prepare_release(path: Path, version: str, released: date | None = None) -> bool:
    """Promote a complete Unreleased section and recreate its empty template."""
    if not SEMVER.fullmatch(version):
        raise ChangelogError(f"invalid semantic version: {version!r}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        _find_release(lines, version)
    except ChangelogError as exc:
        if "found 0" not in str(exc):
            raise
    else:
        validate_document(text, version)
        return False

    start, end = _find_unreleased(lines)
    bodies = _category_bodies(lines, start, end)
    _check_complete_release(bodies, version)
    release_body = "\n".join(lines[start + 1 : end]).strip()
    release_date = released or date.today()
    replacement = (
        "## [Unreleased]\n\n"
        f"{UNRELEASED_TEMPLATE}\n\n"
        f"## [{version}] - {release_date.isoformat()}\n\n"
        f"{release_body}"
    ).splitlines()
    updated = lines[:start] + replacement + lines[end:]
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    return True


def render_release_notes(text: str, version: str) -> str:
    """Return the curated release entry in a GitHub Release-friendly format."""
    validate_document(text, version)
    lines = text.splitlines()
    start, end, released = _find_release(lines, version)
    body = "\n".join(lines[start + 1 : end]).strip()
    return f"Released {released.isoformat()}.\n\n{body}\n"


def _version_from_repository() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_CHANGELOG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate release notes")
    validate.add_argument("--version", default=None)

    prepare = subparsers.add_parser("prepare", help="promote Unreleased to a release")
    prepare.add_argument("version")

    render = subparsers.add_parser("render", help="render notes for a GitHub Release")
    render.add_argument("version")
    render.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "validate":
            version = args.version or _version_from_repository()
            validate_document(args.file.read_text(encoding="utf-8"), version)
            print(f"CHANGELOG.md has complete professional release notes for {version}")
        elif args.command == "prepare":
            changed = prepare_release(args.file, args.version.removeprefix("v"))
            action = "prepared" if changed else "already contains"
            print(f"CHANGELOG.md {action} release {args.version.removeprefix('v')}")
        else:
            notes = render_release_notes(
                args.file.read_text(encoding="utf-8"), args.version.removeprefix("v")
            )
            if args.output:
                args.output.write_text(notes, encoding="utf-8")
            else:
                sys.stdout.write(notes)
    except ChangelogError as exc:
        print(f"changelog check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
