#!/usr/bin/env python3
"""Copy a stable release's changelog section from a release branch to main.

Stable hotfixes are cut from ``release/X.Y`` branches, but the Home Assistant
add-on store and the GitHub Release notes read ``CHANGELOG.md`` on ``main``.
``release.yml`` runs this before it regenerates the add-on directories, so a
hotfix's notes reach both.  A section that main already has is left alone.

Usage::

    carry_stable_changelog.py --version 2.76.6 --source release.md --target CHANGELOG.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)
_FOOTER = re.compile(r"^\[[^\]]+\]: ", re.MULTILINE)
_STABLE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _stable_key(version: str) -> tuple[int, int, int] | None:
    match = _STABLE.match(version)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def _section(text: str, version: str) -> str | None:
    """Return the section for ``version`` up to the next heading or the footer."""
    for match in _HEADING.finditer(text):
        if match.group(1) != version:
            continue
        rest = text[match.end() :]
        ends = [m.start() for m in (_HEADING.search(rest), _FOOTER.search(rest)) if m]
        end = match.end() + min(ends) if ends else len(text)
        return text[match.start() : end].rstrip("\n") + "\n\n"
    return None


def carry_section(target: str, source: str, version: str) -> str:
    """Return ``target`` with ``version``'s section from ``source`` inserted."""
    if _section(target, version) is not None:
        return target
    section = _section(source, version)
    if section is None:
        raise ValueError(f"{version} has no section in the source changelog")
    new_key = _stable_key(version)
    if new_key is None:
        raise ValueError(f"{version} is not a stable version")

    for match in _HEADING.finditer(target):
        key = _stable_key(match.group(1))
        if key is not None and key < new_key:
            return target[: match.start()] + section + target[match.start() :]

    footer = _FOOTER.search(target)
    if footer:
        return target[: footer.start()] + section + target[footer.start() :]
    return target.rstrip("\n") + "\n\n" + section


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True, type=Path, help="changelog of the release branch")
    parser.add_argument("--target", required=True, type=Path, help="main's CHANGELOG.md, rewritten in place")
    args = parser.parse_args(argv)

    target = args.target.read_text(encoding="utf-8")
    try:
        result = carry_section(target, args.source.read_text(encoding="utf-8"), args.version)
    except ValueError as exc:
        print(f"carry_stable_changelog: {exc}", file=sys.stderr)
        return 1
    if result == target:
        print(f"{args.version}: already in {args.target}")
    else:
        args.target.write_text(result, encoding="utf-8")
        print(f"{args.version}: section copied into {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
