"""Verify requirements.txt and pyproject.toml [project.dependencies] declare the same set of packages.

requirements.txt is human-curated with rich per-package documentation comments
(rationale, CVE references, Linux-only notes). pyproject.toml mirrors the
package list in machine-readable form for `pip install .` / build backends.
This script enforces that both stay in sync as **sets** of `name spec` strings,
ignoring comments, blank lines, and order.

Usage:
    python scripts/sync_requirements.py            # report only
    python scripts/sync_requirements.py --check    # exit 1 if out of sync (CI)
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _REPO_ROOT / "requirements.txt"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Strip whitespace and ignore inline trailing comments. We compare full
# `name[extra]op version` strings — extras and version specifiers must match.
_LINE_RE = re.compile(r"^\s*([^\s#][^#]*?)\s*(?:#.*)?$")


def _parse_requirements(text: str) -> set[str]:
    deps: set[str] = set()
    for raw in text.splitlines():
        match = _LINE_RE.match(raw)
        if not match:
            continue
        spec = match.group(1).strip()
        if spec.startswith("-r ") or spec.startswith("--"):
            continue
        deps.add(spec)
    return deps


def _parse_pyproject(text: str) -> set[str]:
    parsed = tomllib.loads(text)
    return set(parsed.get("project", {}).get("dependencies", []))


def main() -> int:
    check = "--check" in sys.argv

    req_text = _REQUIREMENTS.read_text()
    pp_text = _PYPROJECT.read_text()

    req_set = _parse_requirements(req_text)
    pp_set = _parse_pyproject(pp_text)

    missing_from_pp = req_set - pp_set
    missing_from_req = pp_set - req_set

    if not missing_from_pp and not missing_from_req:
        print("requirements.txt and pyproject.toml [project.dependencies] are in sync")
        return 0

    print("OUT OF SYNC: requirements.txt vs pyproject.toml [project.dependencies]", file=sys.stderr)
    if missing_from_pp:
        print("\n  Missing from pyproject.toml:", file=sys.stderr)
        for dep in sorted(missing_from_pp):
            print(f"    + {dep}", file=sys.stderr)
    if missing_from_req:
        print("\n  Missing from requirements.txt:", file=sys.stderr)
        for dep in sorted(missing_from_req):
            print(f"    + {dep}", file=sys.stderr)
    print(
        "\nrequirements.txt is the human-curated source (with comments).\n"
        "pyproject.toml [project.dependencies] mirrors the package list for build/install.\n"
        "Update both manually so they declare the same set of `name op version` strings.",
        file=sys.stderr,
    )
    return 1 if check else 0


if __name__ == "__main__":
    sys.exit(main())
