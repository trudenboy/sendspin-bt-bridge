#!/usr/bin/env python3
"""Compose GitHub release notes from code-range notes plus curated changelog highlights."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_CHANGELOG_HEADER_RE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s+-\s+.+)?$", flags=re.MULTILINE)


def _parse_release_version(version: str) -> tuple[int, int, int, int, int] | None:
    """Return a sortable tuple for stable/rc/beta versions."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(rc|beta)\.(\d+))?$", str(version or "").strip())
    if not match:
        return None
    prerelease = match.group(4)
    prerelease_rank = 2 if prerelease is None else (1 if prerelease == "rc" else 0)
    prerelease_number = int(match.group(5) or 0)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease_rank,
        prerelease_number,
    )


def _iter_changelog_sections(changelog_text: str) -> list[tuple[str, str]]:
    """Return changelog sections in file order as ``(version, body)`` tuples."""
    matches = list(_CHANGELOG_HEADER_RE.finditer(changelog_text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        version = str(match.group("version") or "").strip()
        if not version or version.lower() == "unreleased":
            continue
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog_text)
        sections.append((version, changelog_text[body_start:body_end].strip()))
    return sections


def extract_changelog_section(changelog_text: str, version: str) -> str:
    """Return the Keep a Changelog section body for *version*, without the heading."""
    header_re = re.compile(rf"^## \[{re.escape(version)}\](?:\s+-\s+.+)?$", flags=re.MULTILINE)
    match = header_re.search(changelog_text)
    if not match:
        return ""

    remainder = changelog_text[match.end() :].lstrip("\n")
    next_header = re.search(r"^## \[", remainder, flags=re.MULTILINE)
    section = remainder[: next_header.start()] if next_header else remainder
    return section.strip()


def extract_changelog_range(changelog_text: str, version: str, previous_tag: str = "") -> str:
    """Return aggregated human-written changelog content since the previous stable tag."""
    target_key = _parse_release_version(version)
    previous_version = previous_tag[1:] if previous_tag.startswith("v") else previous_tag
    previous_key = _parse_release_version(previous_version) if previous_version else None
    if not target_key:
        return extract_changelog_section(changelog_text, version)

    sections: list[tuple[str, str]] = []
    for section_version, section_body in _iter_changelog_sections(changelog_text):
        section_key = _parse_release_version(section_version)
        if not section_key or section_key > target_key:
            continue
        if previous_key is not None and section_key <= previous_key:
            continue
        sections.append((section_version, section_body))

    if not sections:
        return extract_changelog_section(changelog_text, version)
    if len(sections) == 1 and not previous_tag and sections[0][0] == version:
        return sections[0][1]
    return "\n\n".join(
        f"### {section_version}\n\n{section_body}".strip() for section_version, section_body in sections if section_body
    ).strip()


def normalize_generated_notes(notes: str) -> str:
    """Return GitHub-generated notes stripped of empty placeholders."""
    normalized = str(notes or "").strip()
    if not normalized:
        return ""
    if normalized.lower() in {"_no changes._", "no changes"}:
        return ""
    return normalized


def normalize_code_change_notes(notes: str) -> str:
    """Return commit-range notes stripped of empty placeholders."""
    normalized = str(notes or "").strip()
    return normalized


def build_release_notes(
    version: str,
    changelog_text: str,
    generated_notes: str = "",
    code_change_notes: str = "",
    *,
    previous_tag: str = "",
) -> str:
    """Compose the final release body for *version*."""
    changelog_section = extract_changelog_range(changelog_text, version, previous_tag)
    generated_section = normalize_generated_notes(generated_notes)
    code_change_section = normalize_code_change_notes(code_change_notes)

    parts: list[str] = []
    full_change_parts: list[str] = []
    if generated_section:
        full_change_parts.append(generated_section)
    if code_change_section:
        full_change_parts.append(f"### Commits in range\n\n{code_change_section}")

    if full_change_parts:
        heading = "## Full code change range"
        if previous_tag:
            heading += f" since `{previous_tag}`"
        parts.append(f"{heading}\n\n" + "\n\n".join(full_change_parts))

    if changelog_section:
        if full_change_parts:
            parts.append(f"## Curated highlights\n\n{changelog_section}")
        else:
            parts.append(changelog_section)

    if not parts:
        raise ValueError(f"Could not build release notes for version {version}")
    return "\n\n".join(parts).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--generated-notes-file", type=Path)
    parser.add_argument("--code-change-notes-file", type=Path)
    parser.add_argument("--previous-tag", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    changelog_text = args.changelog.read_text()
    generated_notes = args.generated_notes_file.read_text() if args.generated_notes_file else ""
    code_change_notes = args.code_change_notes_file.read_text() if args.code_change_notes_file else ""
    body = build_release_notes(
        args.version,
        changelog_text,
        generated_notes,
        code_change_notes,
        previous_tag=args.previous_tag,
    )

    if args.output:
        args.output.write_text(body)
        return
    print(body, end="")


if __name__ == "__main__":
    main()
