#!/usr/bin/env python3
"""Compose GitHub release notes from CHANGELOG.md with an auto-notes fallback."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


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


def normalize_generated_notes(notes: str) -> str:
    """Return GitHub-generated notes stripped of empty placeholders."""
    normalized = str(notes or "").strip()
    if not normalized:
        return ""
    if normalized.lower() in {"_no changes._", "no changes"}:
        return ""
    return normalized


def build_release_notes(version: str, changelog_text: str, generated_notes: str = "") -> str:
    """Compose the final release body for *version*."""
    changelog_section = extract_changelog_section(changelog_text, version)
    generated_section = normalize_generated_notes(generated_notes)

    parts: list[str] = []
    if changelog_section:
        parts.append(changelog_section)
    if generated_section:
        if changelog_section:
            parts.append(f"## GitHub-generated summary\n\n{generated_section}")
        else:
            parts.append(generated_section)

    if not parts:
        raise ValueError(f"Could not build release notes for version {version}")
    return "\n\n".join(parts).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--generated-notes-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    changelog_text = args.changelog.read_text()
    generated_notes = args.generated_notes_file.read_text() if args.generated_notes_file else ""
    body = build_release_notes(args.version, changelog_text, generated_notes)

    if args.output:
        args.output.write_text(body)
        return
    print(body, end="")


if __name__ == "__main__":
    main()
