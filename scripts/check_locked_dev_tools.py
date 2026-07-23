#!/usr/bin/env python3
"""Verify that exact dev-tool pins match the installed lock environment."""

from __future__ import annotations

import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

_EXACT_PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^;\s]+)$")


def main() -> int:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["project"]["optional-dependencies"]["dev"]
    mismatches: list[str] = []

    for requirement in requirements:
        match = _EXACT_PIN.fullmatch(requirement.strip())
        if match is None:
            continue
        package, expected = match.groups()
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{package}: expected {expected}, not installed")
            continue
        print(f"{package}: expected={expected} installed={installed}")
        if installed != expected:
            mismatches.append(f"{package}: expected {expected}, installed {installed}")

    if mismatches:
        print("Locked dev tool mismatch:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"- {mismatch}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
