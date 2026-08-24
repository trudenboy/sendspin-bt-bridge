"""The monitor talks to the manager through a stated interface.

Splitting the monitor loops into their own module did not create a seam: the
module reached into nine private members of ``BluetoothManager`` across thirty
call sites, including writing one of them.  Everything the monitor legitimately
needs is now public on the manager (or on the reconnect policy), so this rule
keeps the seam from silently eroding again — the same shape as the
stdlib-only rule that guards the transport package.
"""

from __future__ import annotations

import ast
import pathlib

_MONITOR = pathlib.Path("src/sendspin_bridge/bluetooth/monitor.py")

#: Dunder attributes are Python's own, not the manager's private state.
_ALLOWED = frozenset()


def _private_manager_attributes() -> list[str]:
    tree = ast.parse(_MONITOR.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if not isinstance(value, ast.Name) or value.id != "mgr":
            continue
        name = node.attr
        if name.startswith("__") or name in _ALLOWED:
            continue
        if name.startswith("_"):
            found.append(f"{name} (line {node.lineno})")
    return found


def test_the_monitor_uses_no_private_manager_state():
    offenders = _private_manager_attributes()

    assert not offenders, "monitor.py reaches into BluetoothManager's private state: " + ", ".join(sorted(offenders))
