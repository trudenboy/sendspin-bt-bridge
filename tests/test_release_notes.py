from __future__ import annotations

import pytest

from scripts.release_notes import build_release_notes, extract_changelog_section


def test_extract_changelog_section_returns_requested_version_only():
    changelog = """
## [Unreleased]

## [2.40.6] - 2026-03-18

### Fixed
- Filled GitHub release notes from CHANGELOG.md

## [2.40.5] - 2026-03-18

### Added
- Previous release
""".strip()

    section = extract_changelog_section(changelog, "2.40.6")

    assert "### Fixed" in section
    assert "Filled GitHub release notes from CHANGELOG.md" in section
    assert "Previous release" not in section


def test_build_release_notes_prefers_changelog_and_appends_generated_summary():
    body = build_release_notes(
        "2.40.6",
        """
## [2.40.6] - 2026-03-18

### Fixed
- Filled GitHub release notes from CHANGELOG.md
""".strip(),
        "## What's Changed\n* Add fallback body",
    )

    assert "### Fixed" in body
    assert "Filled GitHub release notes from CHANGELOG.md" in body
    assert "## GitHub-generated summary" in body
    assert "## What's Changed" in body


def test_build_release_notes_falls_back_to_generated_notes_when_changelog_missing():
    body = build_release_notes("2.40.6", "## [Unreleased]", "## What's Changed\n* Add fallback body")

    assert body.startswith("## What's Changed")


def test_build_release_notes_rejects_empty_result():
    with pytest.raises(ValueError, match="Could not build release notes"):
        build_release_notes("2.40.6", "## [Unreleased]", "")
