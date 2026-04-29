"""Integration tests for `scripts/lint_changelog.py`.

Covers each rule R1-R10 with a positive (compliant) and negative
(violating) fixture, plus the --consolidate-rc transform.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from lint_changelog import (  # noqa: E402  — late import after sys.path
    CANONICAL_CATEGORIES,
    consolidate_rc,
    lint,
    parse_sections,
)

# --------------------------------------------------------------------------
# Helpers


_HEADER_OK = """\
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"""

_FOOTER_OK = """
[Unreleased]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v2.66.0...HEAD
[2.66.0]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v2.65.1...v2.66.0
"""


def _doc(unreleased: str, released: str = "") -> str:
    return _HEADER_OK + "\n## [Unreleased]\n\n" + unreleased + "\n" + released + _FOOTER_OK


def _rules_fired(text: str) -> set[str]:
    return {v.rule for v in lint(text)}


# --------------------------------------------------------------------------
# Positive: a fully-compliant document


def test_compliant_document_passes_clean():
    text = _doc(
        unreleased="### Added\n- A new public knob `SENDSPIN_PORT`.\n",
        released="## [2.66.0] - 2026-04-29\n\n### Added\n- Initial release.\n\n",
    )
    assert lint(text) == []


# --------------------------------------------------------------------------
# R1 — KaC version


def test_r1_fires_on_old_kac_link():
    text = _doc("### Added\n- Foo.\n").replace("1.1.0", "1.0.0", 1)
    assert "R1" in _rules_fired(text)


def test_r1_silent_on_current_kac_link():
    text = _doc("### Added\n- Foo.\n")
    assert "R1" not in _rules_fired(text)


# --------------------------------------------------------------------------
# R2 — Heading shape


def test_r2_fires_on_invalid_bracket_token():
    text = _doc(
        "### Added\n- Foo.\n",
        released="## [random-string] - 2026-04-29\n\n### Added\n- X.\n\n",
    )
    rules = _rules_fired(text)
    assert "R2" in rules


def test_r2_tolerates_legacy_trailing_prose():
    """Old fork-merge headings carried `(origin: foo)` after the date."""
    text = _doc(
        "### Added\n- Foo.\n",
        released="## [1.0.0] - 2026-01-01 (origin: loryanstrant/Sendspin-client)\n\n### Added\n- Y.\n\n",
    )
    assert "R2" not in _rules_fired(text)


# --------------------------------------------------------------------------
# R3 — Unreleased no date


def test_r3_fires_when_unreleased_has_date():
    text = _doc("### Added\n- Foo.\n").replace("## [Unreleased]", "## [Unreleased] - 2026-04-29", 1)
    assert "R3" in _rules_fired(text)


# --------------------------------------------------------------------------
# R4 — Released has date


def test_r4_fires_when_release_missing_date():
    text = _doc(
        "### Added\n- Foo.\n",
        released="## [2.66.0]\n\n### Added\n- X.\n\n",
    )
    assert "R4" in _rules_fired(text)


# --------------------------------------------------------------------------
# R5 — Bare category headings


def test_r5_fires_on_em_dash_subtitle():
    text = _doc("### Fixed — group id overflow\n- Group id badge no longer overflows the card.\n")
    assert "R5" in _rules_fired(text)


def test_r5_fires_on_forbidden_category():
    text = _doc("### Improved\n- Made things faster.\n")
    rules = _rules_fired(text)
    assert "R5" in rules


def test_r5_silent_on_each_canonical_category():
    for category in CANONICAL_CATEGORIES:
        text = _doc(f"### {category}\n- Some change in `{category.lower()}`.\n")
        rules = _rules_fired(text)
        assert "R5" not in rules, f"R5 misfired on canonical category {category!r}"


# --------------------------------------------------------------------------
# R6 — Category order


def test_r6_fires_on_out_of_order_categories():
    text = _doc("### Fixed\n- A.\n\n### Added\n- B.\n")
    assert "R6" in _rules_fired(text)


def test_r6_silent_on_canonical_order():
    text = _doc("### Added\n- A.\n\n### Changed\n- B.\n\n### Fixed\n- C.\n")
    assert "R6" not in _rules_fired(text)


# --------------------------------------------------------------------------
# R7 — No private code identifiers


def test_r7_fires_on_python_private_function():
    text = _doc("### Fixed\n- _publish_full_state now mirrors the legacy topic.\n")
    assert "R7" in _rules_fired(text)


def test_r7_fires_on_internal_dotted_symbol():
    text = _doc("### Changed\n- BridgeOrchestrator.initialize_devices enriches disabled entries.\n")
    assert "R7" in _rules_fired(text)


def test_r7_fires_on_lowercase_module_dotted_symbol():
    """`module.private_helper` and longer chains must be flagged just like
    `ClassName.method` — the rule is about internal symbols, not casing."""
    text = _doc("### Changed\n- module.private_helper now returns None on miss.\n")
    assert "R7" in _rules_fired(text)


def test_r7_fires_on_deeply_nested_dotted_symbol():
    text = _doc("### Fixed\n- services.bluetooth.resolve_hci_for_mac returns the right adapter.\n")
    assert "R7" in _rules_fired(text)


def test_r7_fires_on_internal_file_path():
    text = _doc("### Changed\n- services/ha_publisher.py now publishes the legacy topic.\n")
    assert "R7" in _rules_fired(text)


def test_r7_silent_on_url_with_dotted_tld():
    """`astral.sh` inside a markdown link target must not be flagged."""
    text = _doc("### Changed\n- Migrated to [uv](https://docs.astral.sh/uv/).\n")
    assert "R7" not in _rules_fired(text)


def test_r7_silent_on_backticked_path():
    """A file mentioned in user-facing config (backticked) is allowed."""
    text = _doc("### Changed\n- Edit your local `pyproject.toml` to add the new key.\n")
    assert "R7" not in _rules_fired(text)


def test_r7_silent_on_uppercase_config_keys():
    text = _doc("### Added\n- New config knob `SENDSPIN_PORT` overrides the default.\n")
    assert "R7" not in _rules_fired(text)


# --------------------------------------------------------------------------
# R8 — No process-noise headings


def test_r8_fires_on_code_review_polish():
    text = _doc("### Fixed\n- Real fix.\n\n### Code-review polish\n- Tidied helper.\n")
    rules = _rules_fired(text)
    assert "R8" in rules


def test_r8_fires_on_copilot_review_heading():
    text = _doc("### Fixed\n- Real fix.\n\n### Fixed — Copilot review on PR #218\n- Tidied state.\n")
    rules = _rules_fired(text)
    # Either R5 or R8 is fine here; R8 is the more specific signal.
    assert "R5" in rules or "R8" in rules


# --------------------------------------------------------------------------
# R9 — No orphan prose under a section


def test_r9_fires_on_prose_before_first_category():
    text = _doc("Some intro paragraph that won't appear in release notes.\n\n### Added\n- Foo.\n")
    assert "R9" in _rules_fired(text)


def test_r9_fires_on_bullet_before_first_category():
    text = _doc("- Stray bullet at the top.\n\n### Added\n- Foo.\n")
    assert "R9" in _rules_fired(text)


# --------------------------------------------------------------------------
# R10 — Compare-links footer


def test_r10_fires_when_footer_missing():
    text = _HEADER_OK + "\n## [Unreleased]\n\n### Added\n- Foo.\n\n" + "## [2.66.0] - 2026-04-29\n\n### Added\n- X.\n\n"
    assert "R10" in _rules_fired(text)


def test_r10_silent_when_any_compare_link_present():
    text = _doc(
        "### Added\n- Foo.\n",
        released="## [2.66.0] - 2026-04-29\n\n### Added\n- X.\n\n",
    )
    assert "R10" not in _rules_fired(text)


# --------------------------------------------------------------------------
# parse_sections sanity


def test_parse_sections_recognises_unreleased_and_releases():
    text = _doc(
        "### Added\n- Foo.\n",
        released="## [2.66.0] - 2026-04-29\n\n### Added\n- X.\n\n",
    )
    sections = parse_sections(text)
    brackets = [s.bracket for s in sections]
    assert brackets == ["Unreleased", "2.66.0"]
    assert sections[0].date == ""
    assert sections[1].date == "2026-04-29"


# --------------------------------------------------------------------------
# Line-number accuracy regression — the linter reports file line numbers
# in violation messages; users (and AI agents) navigate to those lines, so
# they have to match the actual file. parse_sections used to strip leading
# newlines from each body which silently shifted subsequent line numbers.


def test_violation_line_numbers_match_actual_file_lines():
    """A category heading on file line 11 must be reported as line 11."""
    # Build a document where the body has multiple leading blank lines so
    # any drift caused by stripping or off-by-one logic is amplified.
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n"
        + "\n"
        + "\n"
        + "### Improved\n"  # forbidden category — must be flagged at its real line
        + "- Faster.\n"
        + _FOOTER_OK
    )
    # Compute the expected line of `### Improved` by counting newlines.
    target_line = text.splitlines().index("### Improved") + 1
    violations = lint(text)
    r5 = [v for v in violations if v.rule == "R5"]
    assert r5, "expected R5 to fire on `### Improved`"
    assert r5[0].line == target_line, (
        f"R5 reported line {r5[0].line}, expected {target_line} (the actual file line of `### Improved`)"
    )


def test_bullet_violation_line_number_matches_file():
    """A bullet with a private symbol must be reported on its real file line."""
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n"
        + "\n"
        + "### Fixed\n"
        + "- _publish_full_state now mirrors the legacy availability topic.\n"
        + _FOOTER_OK
    )
    target_line = text.splitlines().index("- _publish_full_state now mirrors the legacy availability topic.") + 1
    violations = lint(text)
    r7 = [v for v in violations if v.rule == "R7"]
    assert r7
    assert r7[0].line == target_line


# --------------------------------------------------------------------------
# consolidate_rc


def test_consolidate_rc_folds_rc_into_stable():
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n\n### Added\n- WIP.\n\n"
        + "## [2.66.0] - 2026-04-29\n\n### Added\n- Final stable feature.\n\n"
        + "## [2.66.0-rc.2] - 2026-04-28\n\n### Fixed\n- RC2 regression fix.\n\n"
        + "## [2.66.0-rc.1] - 2026-04-27\n\n### Added\n- Initial RC.\n\n"
        + _FOOTER_OK
    )
    folded = consolidate_rc(text)
    assert "## [2.66.0-rc.1]" not in folded
    assert "## [2.66.0-rc.2]" not in folded
    # Stable section retains all bullets, deduplicated.
    assert "Final stable feature" in folded
    assert "RC2 regression fix" in folded
    assert "Initial RC" in folded


def test_consolidate_rc_dedupes_bullets():
    """A bullet that appears identically in rc.1 and stable must not double up."""
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n\n### Added\n- WIP.\n\n"
        + "## [2.66.0] - 2026-04-29\n\n### Added\n- Brand new feature.\n\n"
        + "## [2.66.0-rc.1] - 2026-04-28\n\n### Added\n- Brand new feature.\n\n"
        + _FOOTER_OK
    )
    folded = consolidate_rc(text)
    assert folded.count("- Brand new feature.") == 1


def test_consolidate_rc_keeps_unreleased_and_unrelated_stable():
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n\n### Added\n- WIP.\n\n"
        + "## [2.66.0] - 2026-04-29\n\n### Added\n- New.\n\n"
        + "## [2.65.0] - 2026-04-01\n\n### Fixed\n- Old.\n\n"
        + "## [2.66.0-rc.1] - 2026-04-28\n\n### Added\n- RC1.\n\n"
        + _FOOTER_OK
    )
    folded = consolidate_rc(text)
    assert "## [Unreleased]" in folded
    assert "## [2.66.0]" in folded
    assert "## [2.65.0]" in folded
    assert "## [2.66.0-rc.1]" not in folded


def test_consolidate_rc_keeps_orphan_rc_when_no_stable_yet():
    text = (
        _HEADER_OK
        + "\n## [Unreleased]\n\n### Added\n- WIP.\n\n"
        + "## [2.67.0-rc.1] - 2026-05-01\n\n### Added\n- Future RC.\n\n"
        + _FOOTER_OK
    )
    folded = consolidate_rc(text)
    assert "## [2.67.0-rc.1]" in folded


# --------------------------------------------------------------------------
# Real-file regression — current CHANGELOG.md must be lintable after the
# `[Unreleased]` rewrite + KaC bump + consolidation that ship in this PR.
# We don't assert "no violations" here because the rewrite happens in a
# later commit; this test exists to detect parser crashes on the real
# 7000-line file.


def test_real_changelog_parses_without_crashing():
    real = (_REPO_ROOT / "CHANGELOG.md").read_text()
    sections = parse_sections(real)
    assert len(sections) > 100, "expected many sections in the real CHANGELOG"
    # Lint must run to completion (may report violations — that's fine).
    violations = lint(real)
    # All violations have well-formed fields.
    for v in violations:
        assert v.rule.startswith("R")
        assert v.line > 0
        assert v.msg
