from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from changelog import (  # noqa: E402
    HEADINGS,
    ChangelogError,
    prepare_release,
    render_release_notes,
    validate_document,
)


def _categories(value: str = "None.") -> str:
    return "\n\n".join(f"### {heading}\n\n- {value}" for heading in HEADINGS)


def test_current_release_has_complete_professional_notes() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    validate_document(changelog, version)
    notes = render_release_notes(changelog, version)

    # The date is asserted by shape, not value: hardcoding it makes the test
    # fail on every release, which is the opposite of what it should catch.
    assert re.match(r"^Released \d{4}-\d{2}-\d{2}\.\n", notes), notes[:40]
    # It must also match the date on that release's own heading.
    heading = re.search(rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
                        changelog, re.M)
    assert heading, f"no dated heading for {version}"
    assert notes.startswith(f"Released {heading.group(1)}.\n")
    assert "## [Unreleased]" not in notes
    for heading in HEADINGS:
        assert f"### {heading}" in notes


@pytest.mark.parametrize("version", ["1.18.0", "1.19.0", "1.20.0"])
def test_unpublished_release_notes_are_complete(version: str) -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    validate_document(changelog, version)
    notes = render_release_notes(changelog, version)

    for heading in HEADINGS:
        assert f"### {heading}" in notes


def test_prepare_promotes_curated_notes_and_recreates_template(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    curated = _categories("Reviewed release detail.")
    path.write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{curated}\n\n"
        "## [1.0.0] - 2026-01-01\n\nHistorical notes.\n",
        encoding="utf-8",
    )

    changed = prepare_release(path, "2.0.0", released=date(2026, 8, 4))
    text = path.read_text(encoding="utf-8")

    assert changed is True
    assert "## [2.0.0] - 2026-08-04" in text
    assert text.index("## [Unreleased]") < text.index("## [2.0.0]")
    assert text.index("## [2.0.0]") < text.index("## [1.0.0]")
    assert "Reviewed release detail.\n\n## [1.0.0]" in text
    validate_document(text, "2.0.0")
    assert text.split("## [2.0.0]", maxsplit=1)[0].count("- None yet.") == len(HEADINGS)


def test_prepare_refuses_incomplete_release_notes(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{_categories('None yet.')}\n",
        encoding="utf-8",
    )

    with pytest.raises(ChangelogError, match="still contains placeholder"):
        prepare_release(path, "2.0.0")


def test_release_automation_validates_and_publishes_curated_notes() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'changelog.py prepare "$(VERSION)"' in makefile
    assert "changelog.py validate" in makefile
    assert "make verify-version" in release
    assert "changelog.py render" in release
    assert "--notes-file release-notes.md" in release
    assert "--generate-notes" not in release


def test_version_cannot_be_bumped_without_release_notes() -> None:
    """A bump without notes fails the release and cannot be retriggered.

    The release workflow fires on a VERSION change and validates the changelog
    first, so the version has already moved by the time it fails. The bump
    itself has to refuse.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/set_version.py"), "9.9.9"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0, "set_version.py accepted a version with no notes"
    combined = result.stdout + result.stderr
    assert "refusing to bump" in combined
    assert "make version VERSION=9.9.9" in combined
    # The refusal must happen before anything is rewritten.
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "9.9.9"


def test_every_release_script_is_documented() -> None:
    """A script nobody documents is a script nobody can use correctly."""
    doc = (ROOT / "docs/releasing/RELEASE.md").read_text(encoding="utf-8")
    scripts = {
        path.name
        for path in (ROOT / "scripts").glob("*.py")
        if not path.name.startswith("_")
    }
    undocumented = sorted(s for s in scripts if s not in doc)
    assert not undocumented, f"not documented in RELEASE.md: {undocumented}"


def test_set_version_usage_matches_the_documented_synopsis() -> None:
    """The documented invocation must be the one the script prints."""
    import subprocess

    doc = (ROOT / "docs/releasing/RELEASE.md").read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/set_version.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    usage = (result.stdout + result.stderr).strip()
    assert usage in doc, f"RELEASE.md does not carry the real usage line: {usage!r}"
