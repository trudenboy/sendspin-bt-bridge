#!/usr/bin/env python3
"""Lint CHANGELOG.md against the project's Keep a Changelog discipline.

Enforces the rules documented in CONTRIBUTING.md § Changelog Discipline
(short version in CLAUDE.md). The rule IDs (R1-R10) are stable and
referenced by the linter output and the docs.

Usage:
    python scripts/lint_changelog.py CHANGELOG.md          # check
    python scripts/lint_changelog.py CHANGELOG.md --fix-footer
    python scripts/lint_changelog.py CHANGELOG.md --consolidate-rc
    python scripts/lint_changelog.py CHANGELOG.md --consolidate-rc --dry-run

Exit codes:
    0 — no violations (or --fix-footer / --consolidate-rc applied cleanly)
    1 — violations reported (in check mode) or write failed
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from release_notes import (  # noqa: E402  — import after sys.path tweak
    _bullets_equivalent,
    _iter_section_categories,
)

# --------------------------------------------------------------------------
# Constants — keep in sync with CONTRIBUTING.md § Changelog Discipline.

# Keep a Changelog 1.1.0 canonical category order. Editors see this order
# in CHANGELOG.md; release_notes.py maintains its own sort for GitHub
# release bodies (release_notes.py:152). The two intentionally diverge
# until a future PR aligns them.
CANONICAL_CATEGORIES = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]

# Forbidden category labels — common drift from the canonical set.
FORBIDDEN_CATEGORY_LABELS = {
    "Improved",
    "Refactored",
    "Refactor",
    "Tests",
    "Performance",
    "Notes",
    "Internal",
    "Polish",
    "Cleanup",
}

# Substrings that flag process-noise headings (R8).
PROCESS_NOISE_PATTERNS = [
    re.compile(r"\bcode[- ]review polish\b", re.IGNORECASE),
    re.compile(r"\bcopilot review\b", re.IGNORECASE),
    re.compile(r"\breview fixes\b", re.IGNORECASE),
    re.compile(r"\bround \d+ (?:fixes|review)\b", re.IGNORECASE),
    re.compile(r"\b(?:second|third|fourth)[- ]round\b", re.IGNORECASE),
    re.compile(r"\bfollow[- ]ups? on (?:rc|PR)\b", re.IGNORECASE),
]

# R7 — private code identifiers patterns. Whitelist common false-positives
# (UPPERCASE_CONFIG_KEYS like SENDSPIN_PORT, ENV vars, dunder __init__).
_PRIVATE_FUNC_RE = re.compile(r"\b_[a-z][a-z0-9_]+\b")
# Matches both ``ClassName.method`` (uppercase-led) and lowercase
# module/function dotted paths (``module.fn``,
# ``services.bluetooth.resolve_hci_for_mac``). File-path matches
# (``pyproject.toml``, ``services/foo.py``) are caught earlier by
# ``_FILE_PATH_RE`` so they get the more specific message.
_DOTTED_PRIVATE_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9]*|[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\.[a-z_][a-z0-9_]*\b")
_FILE_PATH_RE = re.compile(r"\b[\w/]+\.(?:py|yml|yaml|toml|sh)\b")

# Heading regex compatible with scripts/generate_ha_addon_variants.py:264.
# Trailing free-form text after the date is tolerated for legacy entries
# (e.g. "## [1.0.0] - 2026-01-01 (origin: loryanstrant/Sendspin-client)").
# New entries should not include trailing prose; that's enforced via R2's
# bracket-token check, not a strict line shape.
_VERSION_HEADING_RE = re.compile(
    r"^## \[(?P<bracket>[^\]]+)\]"
    r"(?:[ \t]+-[ \t]+(?P<date>\d{4}-\d{2}-\d{2})(?P<yanked>[ \t]+\[YANKED\])?)?"
    r"(?P<trailing>[ \t]+.+)?[ \t]*$",
    flags=re.MULTILINE,
)
_VERSION_TOKEN_RE = re.compile(r"^(?:Unreleased|\d+\.\d+\.\d+(?:-(?:rc|beta)\.\d+)?)$")
_KAC_LINK_RE = re.compile(r"keepachangelog\.com/en/(?P<version>\d+\.\d+\.\d+)/")

# --------------------------------------------------------------------------


@dataclass
class Violation:
    rule: str
    line: int
    msg: str

    def render(self) -> str:
        return f"  {self.rule} (line {self.line}): {self.msg}"


@dataclass
class Section:
    """Parsed `## [...]` section."""

    bracket: str  # "Unreleased" or "X.Y.Z" or "X.Y.Z-rc.N"
    date: str  # "YYYY-MM-DD" or ""
    yanked: bool
    line: int  # 1-based line number of the `## [...]` heading
    heading_start: int  # absolute char offset of the `## [...]` heading
    body_start: int  # absolute char offset of the body's first line
    body_end: int  # absolute char offset (start of next heading or len(text))
    body: str  # section body (between heading and next heading), unstripped


def parse_sections(text: str) -> list[Section]:
    """Parse `## [...]` sections.

    ``heading_start`` points at the `#` of the `## [...]` heading;
    ``body_start`` points at the first character of the section body
    (the line right after the heading). The body is intentionally NOT
    stripped so line offsets against ``text`` line up with actual file
    line numbers.
    """
    matches = list(_VERSION_HEADING_RE.finditer(text))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        # Skip past the heading's trailing newline so body_start lands on
        # the first character of the line below the heading.
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        line_no = text.count("\n", 0, match.start()) + 1
        sections.append(
            Section(
                bracket=match.group("bracket").strip(),
                date=(match.group("date") or "").strip(),
                yanked=bool(match.group("yanked")),
                line=line_no,
                heading_start=match.start(),
                body_start=body_start,
                body_end=body_end,
                body=text[body_start:body_end],
            )
        )
    return sections


def _line_number_of_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------
# Rule checks.


def check_r1_kac_version(text: str) -> list[Violation]:
    """R1 — Header references Keep a Changelog 1.1.0."""
    violations: list[Violation] = []
    head = "\n".join(text.splitlines()[:15])
    match = _KAC_LINK_RE.search(head)
    if not match:
        violations.append(Violation("R1", 1, "Header is missing a Keep a Changelog link."))
    elif match.group("version") != "1.1.0":
        # Find the actual line.
        for line_no, line in enumerate(text.splitlines()[:15], start=1):
            if _KAC_LINK_RE.search(line):
                violations.append(
                    Violation(
                        "R1",
                        line_no,
                        f"KaC link points to {match.group('version')}; bump to 1.1.0.",
                    )
                )
                break
    return violations


def check_r2_heading_shape(text: str, sections: list[Section]) -> list[Violation]:
    """R2 — Each `## [...]` heading matches the canonical shape."""
    violations: list[Violation] = []
    # Detect `## [...` lines that did NOT match _VERSION_HEADING_RE.
    matched_lines = {section.line for section in sections}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## [") and line_no not in matched_lines:
            violations.append(
                Violation(
                    "R2",
                    line_no,
                    f"Heading {line!r} does not match `## [VERSION] - YYYY-MM-DD [YANKED]?`.",
                )
            )
    # Validate each parsed section's bracket token.
    for section in sections:
        if not _VERSION_TOKEN_RE.fullmatch(section.bracket):
            violations.append(
                Violation(
                    "R2",
                    section.line,
                    f"Version token [{section.bracket}] is not 'Unreleased' or 'X.Y.Z[-(rc|beta).N]'.",
                )
            )
    return violations


def check_r3_unreleased_no_date(sections: list[Section]) -> list[Violation]:
    """R3 — `[Unreleased]` must not have a date."""
    violations: list[Violation] = []
    for section in sections:
        if section.bracket == "Unreleased" and section.date:
            violations.append(
                Violation(
                    "R3",
                    section.line,
                    f"[Unreleased] must not carry a date (found {section.date!r}).",
                )
            )
    return violations


def check_r4_released_has_date(sections: list[Section]) -> list[Violation]:
    """R4 — Each `[X.Y.Z]` section must have an ISO 8601 date."""
    violations: list[Violation] = []
    for section in sections:
        if section.bracket != "Unreleased" and not section.date:
            violations.append(
                Violation(
                    "R4",
                    section.line,
                    f"[{section.bracket}] is missing a `YYYY-MM-DD` date.",
                )
            )
    return violations


def _is_unreleased(section: Section) -> bool:
    return section.bracket == "Unreleased"


def _iter_section_subheadings(section: Section, text: str) -> Iterable[tuple[int, str]]:
    """Yield (1-based line number, heading text after `### `) within a section.

    Walks ``text`` directly so the reported line number is the actual
    file line, regardless of leading/trailing blank lines in the body.
    """
    cursor = section.body_start
    body = text[section.body_start : section.body_end]
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("### "):
            yield _line_number_of_offset(text, cursor), stripped[4:].strip()
        cursor += len(line)


def check_r5_bare_category(text: str, sections: list[Section]) -> list[Violation]:
    """R5 — Category headings must be bare (single canonical word)."""
    violations: list[Violation] = []
    for section in sections:
        if not _is_unreleased(section):
            continue  # style grandfathered in history
        for line_no, heading in _iter_section_subheadings(section, text):
            if heading in CANONICAL_CATEGORIES:
                continue
            if heading in FORBIDDEN_CATEGORY_LABELS:
                violations.append(
                    Violation(
                        "R5",
                        line_no,
                        f"Forbidden category {heading!r}; use one of {CANONICAL_CATEGORIES}.",
                    )
                )
                continue
            # Either contains punctuation (em-dash subtitle) or is some custom label.
            violations.append(
                Violation(
                    "R5",
                    line_no,
                    f"Heading {heading!r} is not a bare category. Use bare `### Added/Changed/Deprecated/Removed/Fixed/Security` plus bullets underneath.",
                )
            )
    return violations


def check_r6_category_order(text: str, sections: list[Section]) -> list[Violation]:
    """R6 — Categories under one section follow the canonical order."""
    violations: list[Violation] = []
    canonical_index = {name: i for i, name in enumerate(CANONICAL_CATEGORIES)}
    for section in sections:
        if not _is_unreleased(section):
            continue
        seen: list[tuple[int, int, str]] = []  # (line_no, index, heading)
        for line_no, heading in _iter_section_subheadings(section, text):
            if heading in canonical_index:
                seen.append((line_no, canonical_index[heading], heading))
        for prev, curr in pairwise(seen):
            if curr[1] < prev[1]:
                violations.append(
                    Violation(
                        "R6",
                        curr[0],
                        f"Category `### {curr[2]}` appears after `### {prev[2]}`; canonical order is {CANONICAL_CATEGORIES}.",
                    )
                )
    return violations


def _iter_bullets(section: Section, text: str) -> Iterable[tuple[int, str]]:
    """Yield (1-based line number, bullet text without the leading `- `).

    Walks ``text`` directly so the reported line number is the actual
    file line, regardless of leading/trailing blank lines in the body.
    """
    cursor = section.body_start
    body = text[section.body_start : section.body_end]
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("- "):
            yield _line_number_of_offset(text, cursor), line.lstrip()[2:].strip()
        cursor += len(line)


def _strip_markup(text: str) -> str:
    """Return *text* with URLs, markdown link targets, and inline-code spans masked.

    Replaces matched spans with same-length whitespace so byte offsets are
    preserved if a caller wants to map back. Used to suppress false positives
    in R7 (e.g. `astral.sh` inside `[uv](https://docs.astral.sh/uv/)`).
    """

    def _blank(match: re.Match[str]) -> str:
        return " " * (match.end() - match.start())

    # Strip URLs first (covers bare URLs and markdown link targets).
    text = re.sub(r"https?://\S+", _blank, text)
    # Strip markdown link targets `(...)` after a `]`.
    text = re.sub(r"\]\(([^)]+)\)", lambda m: "]" + " " * (len(m.group(0)) - 1), text)
    # Strip inline code spans `...` (single backtick).
    text = re.sub(r"`[^`]+`", _blank, text)
    return text


def check_r7_no_private_identifiers(text: str, sections: list[Section]) -> list[Violation]:
    """R7 — bullets must not name private symbols / file paths."""
    violations: list[Violation] = []
    for section in sections:
        if not _is_unreleased(section):
            continue
        for line_no, bullet in _iter_bullets(section, text):
            scrubbed = _strip_markup(bullet)
            file_hit = _FILE_PATH_RE.search(scrubbed)
            if file_hit:
                violations.append(
                    Violation(
                        "R7",
                        line_no,
                        f"File path {file_hit.group(0)!r} in bullet — describe behaviour, not file. "
                        "(Internal paths rot through refactors; commit message keeps the link. "
                        "Wrap the path in backticks if you really need to name the file.)",
                    )
                )
                continue
            priv = _PRIVATE_FUNC_RE.search(scrubbed)
            if priv:
                violations.append(
                    Violation(
                        "R7",
                        line_no,
                        f"Private identifier {priv.group(0)!r} in bullet — describe behaviour from the user's POV instead.",
                    )
                )
                continue
            dotted = _DOTTED_PRIVATE_RE.search(scrubbed)
            if dotted:
                violations.append(
                    Violation(
                        "R7",
                        line_no,
                        f"Internal symbol {dotted.group(0)!r} in bullet — describe behaviour, not implementation.",
                    )
                )
    return violations


def check_r8_no_process_noise(text: str, sections: list[Section]) -> list[Violation]:
    """R8 — no process-noise headings (`Code-review polish`, `Round 2 fixes`, etc)."""
    violations: list[Violation] = []
    for section in sections:
        if not _is_unreleased(section):
            continue
        for line_no, heading in _iter_section_subheadings(section, text):
            for pat in PROCESS_NOISE_PATTERNS:
                if pat.search(heading):
                    violations.append(
                        Violation(
                            "R8",
                            line_no,
                            f"Process-noise heading: {heading!r}. Re-categorize as Added/Changed/Fixed and let git log own the process.",
                        )
                    )
                    break
    return violations


def check_r9_no_orphan_prose(text: str, sections: list[Section]) -> list[Violation]:
    """R9 — warn when a section has prose between `##` and the first `### Category`.

    Such prose does not appear in GitHub release notes (release_notes.py
    starts collecting bullets only after the first `###`). Heuristic — emit
    as a soft warning (still listed; doesn't necessarily fail the build
    if no other rules fired). Only checked under [Unreleased].
    """
    violations: list[Violation] = []
    for section in sections:
        if not _is_unreleased(section):
            continue
        cursor = section.body_start
        body = text[section.body_start : section.body_end]
        for raw_line in body.splitlines(keepends=True):
            line = raw_line.strip()
            line_no = _line_number_of_offset(text, cursor)
            cursor += len(raw_line)
            if not line:
                continue
            if line.startswith("### "):
                break
            if line.startswith("- "):
                # Bare bullets without a category header — not strictly
                # orphan prose, but also not collectable; flag separately.
                violations.append(
                    Violation(
                        "R9",
                        line_no,
                        "Bullet appears before any `### Category` heading; release_notes.py will skip it. Move it under a category.",
                    )
                )
                continue
            violations.append(
                Violation(
                    "R9",
                    line_no,
                    "Prose between `##` and first `### Category` does not appear in release notes. Move it inside a category as a bullet, or delete.",
                )
            )
    return violations


def check_r10_compare_links_footer(text: str, sections: list[Section]) -> list[Violation]:
    """R10 — file ends with a compare-links footer covering each section."""
    violations: list[Violation] = []
    # Look for at least one `[X.Y.Z]: https://...compare/...` line near the
    # end of the file. We don't require completeness here (the --fix-footer
    # mode regenerates); we only require the footer block to exist when
    # there are tagged releases.
    has_any = re.search(
        r"^\[(?:Unreleased|\d+\.\d+\.\d+(?:-(?:rc|beta)\.\d+)?)\]:\s+https://",
        text,
        flags=re.MULTILINE,
    )
    if not has_any and any(s.bracket != "Unreleased" for s in sections):
        violations.append(
            Violation(
                "R10",
                len(text.splitlines()),
                "Missing compare-links footer. Run `python scripts/lint_changelog.py CHANGELOG.md --fix-footer`.",
            )
        )
    return violations


# --------------------------------------------------------------------------
# Footer generation (--fix-footer).

_REPO_URL_DEFAULT = "https://github.com/trudenboy/sendspin-bt-bridge"


def _git_tags(repo_root: Path) -> list[str]:
    """Return tags in chronological order (oldest first)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "tag", "--list", "v*", "--sort=creatordate"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _build_footer(text: str, sections: list[Section], repo_root: Path) -> str:
    repo_url = _REPO_URL_DEFAULT
    tagged = _git_tags(repo_root)
    tagset = set(tagged)
    # Latest tag for [Unreleased] compare base.
    latest = tagged[-1] if tagged else ""
    lines: list[str] = []
    if any(_is_unreleased(s) for s in sections) and latest:
        lines.append(f"[Unreleased]: {repo_url}/compare/{latest}...HEAD")
    # For each released section, link to compare with previous tag (if both exist).
    released = [s for s in sections if not _is_unreleased(s)]
    for index, section in enumerate(released):
        version = section.bracket
        tag = f"v{version}"
        if tag not in tagset:
            continue
        # Previous tag = the next released section in file order (file is
        # newest-first).
        prev_section = released[index + 1] if index + 1 < len(released) else None
        prev_tag = f"v{prev_section.bracket}" if prev_section else ""
        if prev_tag and prev_tag in tagset:
            lines.append(f"[{version}]: {repo_url}/compare/{prev_tag}...{tag}")
        else:
            lines.append(f"[{version}]: {repo_url}/releases/tag/{tag}")
    return "\n".join(lines)


def fix_footer(text: str, repo_root: Path) -> str:
    """Strip any existing footer block and append a freshly generated one."""
    sections = parse_sections(text)
    new_footer = _build_footer(text, sections, repo_root)
    if not new_footer:
        return text
    # Strip trailing footer (any line matching the link-ref shape) and
    # surrounding blank lines.
    body = re.sub(
        r"(?:\n[ \t]*\n)?(?:^\[(?:Unreleased|\d+\.\d+\.\d+(?:-(?:rc|beta)\.\d+)?)\]:[^\n]*\n?)+\Z",
        "",
        text,
        flags=re.MULTILINE,
    ).rstrip()
    return body + "\n\n" + new_footer + "\n"


# --------------------------------------------------------------------------
# RC consolidation (--consolidate-rc).


def _is_prerelease(bracket: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+-(?:rc|beta)\.\d+", bracket))


def _stable_base(bracket: str) -> str | None:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-(?:rc|beta)\.\d+)?", bracket)
    return match.group(1) if match else None


def consolidate_rc(text: str) -> str:
    """Fold each X.Y.Z-{rc,beta}.N section into the matching X.Y.Z stable.

    Algorithm:
      * Walk sections in file order (newest-first).
      * For each prerelease, locate the stable section with the matching
        X.Y.Z base. If found, append rc bullets (deduplicated via
        _bullets_equivalent) under their existing category headings within
        the stable section. Drop the prerelease section.
      * If no stable exists for a prerelease (e.g. an in-progress rc with
        no final yet), keep the prerelease section as-is.
    """
    sections = parse_sections(text)
    if not sections:
        return text

    # Group prereleases by stable base.
    pre_by_base: dict[str, list[Section]] = {}
    stable_by_bracket: dict[str, Section] = {}
    for section in sections:
        if _is_unreleased(section):
            continue
        if _is_prerelease(section.bracket):
            base = _stable_base(section.bracket)
            if base:
                pre_by_base.setdefault(base, []).append(section)
        else:
            stable_by_bracket[section.bracket] = section

    # For each stable that has prereleases, fold their bullets.
    rewritten_bodies: dict[int, str] = {}  # section.line -> new body
    drop_sections: set[int] = set()  # section.line of prereleases to drop

    for base, prereleases in pre_by_base.items():
        stable = stable_by_bracket.get(base)
        if not stable:
            continue  # leave prereleases alone (no stable yet)

        # Parse stable categories (preserving order + headings).
        stable_groups = _iter_section_categories(stable.body)
        # Convert to mutable representation: ordered list of [category,
        # list[bullet]].
        stable_buckets: list[list] = [[cat, list(bullets)] for cat, bullets in stable_groups]

        # Walk prereleases in chronological order (oldest rc first → newest).
        # Sections are in file order which is newest-first; reverse them.
        for pre in reversed(prereleases):
            for category, bullets in _iter_section_categories(pre.body):
                # Find matching bucket in stable.
                bucket = next((b for b in stable_buckets if b[0] == category), None)
                if bucket is None:
                    bucket = [category, []]
                    stable_buckets.append(bucket)
                for bullet in bullets:
                    if any(_bullets_equivalent(existing, bullet) for existing in bucket[1]):
                        continue
                    bucket[1].append(bullet)
            drop_sections.add(pre.line)

        # Re-render the stable body. We preserve any leading prose (lines
        # before the first ### Category) verbatim, then emit categories.
        leading_prose = _extract_leading_prose(stable.body)
        rendered_body = _render_section_body(leading_prose, stable_buckets)
        rewritten_bodies[stable.line] = rendered_body

    # Stitch the file back together.
    out_lines: list[str] = []
    cursor = 0
    for section in sections:
        # Emit text between previous section's end and this heading.
        out_lines.append(text[cursor : section.heading_start])
        if section.line in drop_sections:
            cursor = section.body_end
            continue
        # Re-emit the heading line (including its trailing newline) verbatim.
        out_lines.append(text[section.heading_start : section.body_start])
        body = rewritten_bodies.get(section.line)
        if body is None:
            # Unmodified — emit original body untouched.
            out_lines.append(text[section.body_start : section.body_end])
        else:
            # New body: blank line after heading, body, blank line before
            # next section heading.
            out_lines.append("\n" + body.rstrip() + "\n\n")
        cursor = section.body_end

    out_lines.append(text[cursor:])
    return "".join(out_lines)


def _extract_leading_prose(body: str) -> str:
    """Return any prose that appears before the first `### …` heading in body."""
    lines = body.splitlines()
    prose: list[str] = []
    for line in lines:
        if line.lstrip().startswith("### "):
            break
        prose.append(line)
    # Trim trailing blanks.
    while prose and not prose[-1].strip():
        prose.pop()
    return "\n".join(prose)


def _render_section_body(leading_prose: str, buckets: list[list]) -> str:
    parts: list[str] = []
    if leading_prose.strip():
        parts.append(leading_prose.strip())
    for category, bullets in buckets:
        if not bullets:
            continue
        section_lines = [f"### {category}"]
        for bullet in bullets:
            section_lines.append(f"- {bullet}")
        parts.append("\n".join(section_lines))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# CLI.


def lint(text: str) -> list[Violation]:
    sections = parse_sections(text)
    violations: list[Violation] = []
    violations.extend(check_r1_kac_version(text))
    violations.extend(check_r2_heading_shape(text, sections))
    violations.extend(check_r3_unreleased_no_date(sections))
    violations.extend(check_r4_released_has_date(sections))
    violations.extend(check_r5_bare_category(text, sections))
    violations.extend(check_r6_category_order(text, sections))
    violations.extend(check_r7_no_private_identifiers(text, sections))
    violations.extend(check_r8_no_process_noise(text, sections))
    violations.extend(check_r9_no_orphan_prose(text, sections))
    violations.extend(check_r10_compare_links_footer(text, sections))
    violations.sort(key=lambda v: (v.line, v.rule))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to CHANGELOG.md")
    parser.add_argument(
        "--fix-footer",
        action="store_true",
        help="Regenerate compare-links footer from `git tag`.",
    )
    parser.add_argument(
        "--consolidate-rc",
        action="store_true",
        help="Fold X.Y.Z-rc.N / -beta.N sections into matching X.Y.Z stable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix-footer / --consolidate-rc: show diff without writing.",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 1
    original = args.path.read_text()
    repo_root = args.path.resolve().parent

    if args.fix_footer or args.consolidate_rc:
        new_text = original
        if args.consolidate_rc:
            new_text = consolidate_rc(new_text)
        if args.fix_footer:
            new_text = fix_footer(new_text, repo_root)
        if new_text == original:
            print(f"{args.path}: no changes")
            return 0
        if args.dry_run:
            old_lines = original.splitlines()
            new_lines = new_text.splitlines()
            print(f"{args.path}: would change {len(old_lines)} → {len(new_lines)} lines")
            return 0
        args.path.write_text(new_text)
        old_lines = original.splitlines()
        new_lines = new_text.splitlines()
        print(f"{args.path}: rewrote {len(old_lines)} → {len(new_lines)} lines")
        return 0

    violations = lint(original)
    if not violations:
        print(f"{args.path}: clean")
        return 0
    print(f"{args.path}: {len(violations)} violation(s)", file=sys.stderr)
    for v in violations:
        print(v.render(), file=sys.stderr)
    print(
        "\nSee CONTRIBUTING.md § Changelog Discipline for the full ruleset.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
