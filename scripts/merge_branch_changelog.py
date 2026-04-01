#!/usr/bin/env python3
"""Merge changelog sections from a branch into the main CHANGELOG.md.

Used by CI to inject beta/rc entries (which live on their own branch)
into the main-branch CHANGELOG.md before running addon-sync.

Usage:
    python3 scripts/merge_branch_changelog.py CHANGELOG.md /tmp/branch-changelog.md
"""

from __future__ import annotations

import re
import sys

_HEADING_RE = re.compile(
    r"^## \[(?P<version>[^\]]+)\](?:[ \t]+-[ \t]+.+)?$",
    flags=re.MULTILINE,
)


def merge(main_path: str, branch_path: str) -> None:
    with open(main_path) as f:
        main_text = f.read()
    with open(branch_path) as f:
        branch_text = f.read()

    main_versions = {m.group("version") for m in _HEADING_RE.finditer(main_text)}

    branch_matches = list(_HEADING_RE.finditer(branch_text))
    new_sections: list[str] = []
    for i, m in enumerate(branch_matches):
        ver = m.group("version")
        if ver == "Unreleased" or ver in main_versions:
            continue
        end = branch_matches[i + 1].start() if i + 1 < len(branch_matches) else len(branch_text)
        new_sections.append(branch_text[m.start() : end].strip())

    if not new_sections:
        print("No new changelog sections to merge")
        return

    # Insert after [Unreleased] heading, before first release entry
    first_release = None
    for m in _HEADING_RE.finditer(main_text):
        if m.group("version") != "Unreleased":
            first_release = m
            break

    insert_pos = first_release.start() if first_release else len(main_text)
    merged = main_text[:insert_pos] + "\n".join(new_sections) + "\n\n" + main_text[insert_pos:]
    with open(main_path, "w") as f:
        f.write(merged)
    print(f"Merged {len(new_sections)} changelog section(s) from branch")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <main-changelog> <branch-changelog>", file=sys.stderr)
        sys.exit(1)
    merge(sys.argv[1], sys.argv[2])
