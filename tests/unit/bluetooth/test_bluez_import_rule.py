"""Hard import rule for ``bluetooth/bluez/``: stdlib only.

The module is the lowest layer of the Bluetooth stack — it must stay
importable from ``services.*``, ``bluetooth.manager`` and ``web.*``
without a cycle, so it may not import anything from the project itself
(no ``services.*``, no ``bluetooth.manager``/``bluetooth.dbus``, no
``config``, no ``web``, no Flask).  ``bluetooth/__init__.py`` stays empty
for the same reason.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BLUEZ_DIR = Path(__file__).parents[3] / "src" / "sendspin_bridge" / "bluetooth" / "bluez"
BLUETOOTH_INIT = BLUEZ_DIR.parent / "__init__.py"


def _imported_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


def test_bluez_package_imports_stdlib_only():
    offenders: list[str] = []
    for path in sorted(BLUEZ_DIR.glob("*.py")):
        for root in _imported_roots(path):
            if root not in sys.stdlib_module_names:
                offenders.append(f"{path.name}: {root}")
    assert not offenders, f"non-stdlib imports in bluetooth/bluez: {offenders}"


def test_bluetooth_package_init_stays_empty():
    assert BLUETOOTH_INIT.read_text(encoding="utf-8").strip() == ""
