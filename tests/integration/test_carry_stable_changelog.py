"""A stable hotfix released from a release branch must reach main's changelog.

The add-on store and the GitHub Release notes are built from main's
CHANGELOG.md, while a hotfix such as 2.76.5 is cut from ``release/2.76`` and
only that branch's changelog has its section.  ``carry_section`` copies the
section across so both read the released notes.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.carry_stable_changelog import carry_section

ROOT = Path(__file__).resolve().parents[2]

_RELEASE_BRANCH = textwrap.dedent(
    """\
    # Changelog

    ## [Unreleased]

    ## [2.76.6] - 2026-10-27

    ### Fixed

    - Bug reports keep their log lines.

    ## [2.76.5] - 2026-10-26

    ### Fixed

    - Tokens survive a settings save.
    """
)

_MAIN = textwrap.dedent(
    """\
    # Changelog

    ## [Unreleased]

    ## [2.77.0-rc.6] - 2026-10-26

    ### Changed

    - Library update.

    ## [2.76.5] - 2026-10-26

    ### Fixed

    - Tokens survive a settings save.

    ## [2.76.4] - 2026-10-12

    ### Fixed

    - Volume buttons.

    [Unreleased]: https://example.invalid/compare/v2.77.0-rc.6...HEAD
    """
)


def test_section_lands_above_the_previous_stable_release():
    result = carry_section(_MAIN, _RELEASE_BRANCH, "2.76.6")

    headings = [line for line in result.splitlines() if line.startswith("## [")]
    assert headings == [
        "## [Unreleased]",
        "## [2.77.0-rc.6] - 2026-10-26",
        "## [2.76.6] - 2026-10-27",
        "## [2.76.5] - 2026-10-26",
        "## [2.76.4] - 2026-10-12",
    ]
    assert "- Bug reports keep their log lines.\n\n## [2.76.5]" in result


def test_a_section_main_already_has_is_left_alone():
    assert carry_section(_MAIN, _RELEASE_BRANCH, "2.76.5") == _MAIN


def test_a_release_newer_than_every_stable_section_lands_above_the_newest_one():
    main = _MAIN.replace("## [2.76.5]", "## [2.75.9]").replace("## [2.76.4]", "## [2.75.8]")
    source = _RELEASE_BRANCH.replace("2.76.6", "2.80.0")

    result = carry_section(main, source, "2.80.0")

    headings = [line for line in result.splitlines() if line.startswith("## [")]
    assert headings.index("## [2.80.0] - 2026-10-27") == headings.index("## [2.75.9] - 2026-10-26") - 1


def test_missing_source_section_is_an_error():
    try:
        carry_section(_MAIN, _RELEASE_BRANCH, "2.76.9")
    except ValueError as exc:
        assert "2.76.9" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cli_rewrites_the_target_and_the_result_passes_the_linter(tmp_path):
    source = tmp_path / "release.md"
    target = tmp_path / "CHANGELOG.md"
    source.write_text(_RELEASE_BRANCH)
    target.write_text((ROOT / "CHANGELOG.md").read_text())
    before = target.read_text()

    version = "2.76.99"
    source.write_text(_RELEASE_BRANCH.replace("2.76.6", version))
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "carry_stable_changelog.py"),
            "--version",
            version,
            "--source",
            str(source),
            "--target",
            str(target),
        ],
        check=True,
    )

    after = target.read_text()
    assert after != before
    assert f"## [{version}] - 2026-10-27" in after
    lint = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lint_changelog.py"), str(target)],
        capture_output=True,
        text=True,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_without_any_stable_section_it_goes_above_the_footer():
    main = textwrap.dedent(
        """\
        ## [Unreleased]

        ## [3.0.0-rc.1] - 2026-10-20

        ### Added

        - Something.

        [Unreleased]: https://example.invalid/compare/v3.0.0-rc.1...HEAD
        """
    )

    result = carry_section(main, _RELEASE_BRANCH, "2.76.6")

    assert result.index("## [2.76.6]") > result.index("- Something.")
    assert result.index("## [2.76.6]") < result.index("[Unreleased]: ")
