from __future__ import annotations

import textwrap

import pytest

from scripts.release_notes import build_release_notes, extract_changelog_range, extract_changelog_section


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


def test_build_release_notes_includes_full_change_range_before_curated_highlights():
    body = build_release_notes(
        "2.40.6",
        textwrap.dedent(
            """
            ## [2.40.6-rc.1] - 2026-03-18

            ### Added
            - RC user-facing summary

            ## [2.40.6] - 2026-03-18

            ### Changed
            - Stable wrap-up

            ## [2.40.5] - 2026-03-17

            ### Added
            - Previous stable release
             """
        ).strip(),
        previous_tag="v2.40.5",
        compare_url="https://github.com/example/repo/compare/v2.40.5...v2.40.6",
    )

    assert "## Cumulative changes since `v2.40.5`" in body
    assert "### Added" in body
    assert "RC user-facing summary" in body
    assert "### Changed" in body
    assert "Stable wrap-up" in body
    assert "Previous stable release" not in body
    assert "### 2.40.6-rc.1" not in body
    assert "Commits in range" not in body
    assert "**Full Changelog**: https://github.com/example/repo/compare/v2.40.5...v2.40.6" in body


def test_build_release_notes_falls_back_to_generated_notes_when_changelog_missing():
    body = build_release_notes("2.40.6", "## [Unreleased]", "## What's Changed\n* Add fallback body")

    assert body.startswith("## What's Changed")
    assert "## What's Changed" in body


def test_build_release_notes_deduplicates_near_duplicate_cumulative_bullets():
    body = build_release_notes(
        "2.40.6",
        textwrap.dedent(
            """
            ## [2.40.6] - 2026-03-18

            ### Added
            - Diagnostics recovery tooling now includes a deeper retained recovery timeline with advanced severity, scope, source, and window filters for power-user trace review.

            ## [2.40.6-rc.1] - 2026-03-18

            ### Added
            - Diagnostics recovery timeline now retains a deeper event window and exposes advanced severity, scope, source, and window filters for power-user trace review.
            """
        ).strip(),
        previous_tag="v2.40.5",
    )

    assert body.count("power-user trace review") == 1


def test_extract_changelog_range_aggregates_entries_since_previous_stable():
    section = extract_changelog_range(
        textwrap.dedent(
            """
            ## [Unreleased]

            ## [2.40.6] - 2026-03-18

            ### Changed
            - Stable wrap-up

            ## [2.40.6-rc.1] - 2026-03-18

            ### Added
            - RC user-facing summary

            ## [2.40.5] - 2026-03-17

            ### Added
            - Previous stable release
            """
        ).strip(),
        "2.40.6",
        "v2.40.5",
    )

    assert "### Changed" in section
    assert "### Added" in section
    assert "### 2.40.6" not in section
    assert "### 2.40.6-rc.1" not in section
    assert "Previous stable release" not in section


def test_build_release_notes_rejects_empty_result():
    with pytest.raises(ValueError, match="Could not build release notes"):
        build_release_notes("2.40.6", "## [Unreleased]", "")
