#!/usr/bin/env python3
"""Compose cumulative GitHub release notes from curated changelog highlights."""

from __future__ import annotations

import argparse
import re
import string
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


def _select_changelog_range(changelog_text: str, version: str, previous_tag: str = "") -> list[tuple[str, str]]:
    """Return all changelog sections that fall within the requested stable release range."""
    target_key = _parse_release_version(version)
    previous_version = previous_tag[1:] if previous_tag.startswith("v") else previous_tag
    previous_key = _parse_release_version(previous_version) if previous_version else None
    if not target_key:
        section = extract_changelog_section(changelog_text, version)
        return [(version, section)] if section else []

    sections: list[tuple[str, str]] = []
    for section_version, section_body in _iter_changelog_sections(changelog_text):
        section_key = _parse_release_version(section_version)
        if not section_key or section_key > target_key:
            continue
        if previous_key is not None and section_key <= previous_key:
            continue
        sections.append((section_version, section_body))

    if sections:
        return sections

    section = extract_changelog_section(changelog_text, version)
    return [(version, section)] if section else []


def _iter_section_categories(section_body: str) -> list[tuple[str, list[str]]]:
    """Return ``(category, bullets)`` groups parsed from a changelog section body."""
    categories: list[tuple[str, list[str]]] = []
    current_category = ""
    current_bullets: list[str] = []

    def flush() -> None:
        nonlocal current_category, current_bullets
        if current_category and current_bullets:
            categories.append((current_category, current_bullets))
        current_category = ""
        current_bullets = []

    for raw_line in str(section_body or "").splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            flush()
            current_category = line[4:].strip()
            continue
        if line.startswith("- "):
            current_bullets.append(line[2:].strip())
            continue
        if current_bullets and line:
            current_bullets[-1] = f"{current_bullets[-1]} {line}".strip()
    flush()
    return categories


def _normalize_bullet_key(text: str) -> str:
    """Return a normalized representation for exact bullet deduplication."""
    return " ".join(
        str(text or "").lower().translate(str.maketrans("", "", string.punctuation.replace("`", ""))).split()
    )


def _bullet_token_set(text: str) -> set[str]:
    """Return a token set for fuzzy duplicate detection."""
    return {token for token in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(token) >= 3 or token.isdigit()}


def _bullets_equivalent(left: str, right: str) -> bool:
    """Return True when two changelog bullets describe the same user-facing change."""
    left_key = _normalize_bullet_key(left)
    right_key = _normalize_bullet_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if len(left_key) >= 48 and len(right_key) >= 48 and (left_key in right_key or right_key in left_key):
        return True

    left_tokens = _bullet_token_set(left)
    right_tokens = _bullet_token_set(right)
    if not left_tokens or not right_tokens:
        return False

    overlap = len(left_tokens & right_tokens)
    smaller = min(len(left_tokens), len(right_tokens))
    return smaller > 0 and (overlap / smaller) >= 0.8


def extract_changelog_range(changelog_text: str, version: str, previous_tag: str = "") -> str:
    """Return cumulative changelog highlights grouped by Keep a Changelog category."""
    sections = _select_changelog_range(changelog_text, version, previous_tag)
    if not sections:
        return ""
    if len(sections) == 1 and sections[0][0] == version and not previous_tag:
        return sections[0][1]

    category_order = ["Added", "Changed", "Fixed", "Removed", "Deprecated", "Security"]
    grouped: dict[str, list[str]] = {}
    for _, section_body in sections:
        for category, bullets in _iter_section_categories(section_body):
            category_bucket = grouped.setdefault(category, [])
            for bullet in bullets:
                if any(_bullets_equivalent(existing, bullet) for existing in category_bucket):
                    continue
                category_bucket.append(bullet)

    ordered_categories = [category for category in category_order if grouped.get(category)]
    ordered_categories.extend(
        category for category in grouped if category not in category_order and grouped.get(category)
    )
    return "\n\n".join(
        f"### {category}\n" + "\n".join(f"- {bullet}" for bullet in grouped[category])
        for category in ordered_categories
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
    *,
    previous_tag: str = "",
    compare_url: str = "",
) -> str:
    """Compose the final release body for *version*."""
    changelog_section = extract_changelog_range(changelog_text, version, previous_tag)
    generated_section = normalize_generated_notes(generated_notes)

    parts: list[str] = []
    if changelog_section:
        heading = "## Cumulative changes"
        if previous_tag:
            heading += f" since `{previous_tag}`"
        parts.append(f"{heading}\n\n{changelog_section}")
    elif generated_section:
        parts.append(generated_section)

    if compare_url:
        parts.append(f"**Full Changelog**: {compare_url}")
    if not parts:
        raise ValueError(f"Could not build release notes for version {version}")
    return "\n\n".join(parts).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--generated-notes-file", type=Path)
    parser.add_argument("--previous-tag", default="")
    parser.add_argument("--compare-url", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    changelog_text = args.changelog.read_text()
    generated_notes = args.generated_notes_file.read_text() if args.generated_notes_file else ""
    body = build_release_notes(
        args.version,
        changelog_text,
        generated_notes,
        previous_tag=args.previous_tag,
        compare_url=args.compare_url,
    )

    if args.output:
        args.output.write_text(body)
        return
    print(body, end="")


if __name__ == "__main__":
    main()
