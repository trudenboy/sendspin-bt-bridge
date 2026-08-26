"""Every fact the normalised state reads must have something that writes it.

`DeviceSnapshot.extra` is an untyped bag: the whole runtime status dict, plus
three dozen keys the snapshot builder adds by hand.  The normalised state, the
capability builder and the guidance all reach into it by string.  A key that
nobody writes is not an error there — it is a `None` that reaches the screen,
which is how the reconnect limit and the audio sink name both came to be
reported as missing on devices that had them.

This walks the readers and the writers and insists the first is covered by
the second.  It is a coarse rule — it cannot tell a genuinely optional key
from a misspelled one — so keys that are legitimately absent are listed here
by name, with the reason.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).parents[4] / "src" / "sendspin_bridge"
CLIENT = SRC / "bridge" / "client.py"

#: Keys read from ``extra`` that no producer writes, on purpose.  Empty, and
#: worth keeping that way: every entry here is a read that can only answer
#: ``None``, which is what this test exists to catch.
KNOWN_ABSENT: set[str] = set()


def _device_status_fields() -> set[str]:
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DeviceStatus":
            return {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    raise AssertionError("DeviceStatus not found — this test needs rewiring")


def _written_extra_keys() -> set[str]:
    written: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and getattr(target.value, "attr", None) == "extra"
                ):
                    written.add(target.slice.value)
    return written


def _read_extra_keys(path: pathlib.Path) -> dict[str, int]:
    """``{key: line}`` for every ``extra[...]`` / ``extra.get(...)`` read."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            base = node.func.value
            if (getattr(base, "attr", None) or getattr(base, "id", None)) == "extra":
                found.setdefault(node.args[0].value, node.lineno)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            base = node.value
            if (getattr(base, "attr", None) or getattr(base, "id", None)) == "extra":
                found.setdefault(node.slice.value, node.lineno)
    return found


def test_every_fact_the_readers_ask_for_is_one_something_writes():
    produced = _device_status_fields() | _written_extra_keys() | KNOWN_ABSENT
    orphans: list[str] = []

    for path in SRC.rglob("*.py"):
        for key, line in _read_extra_keys(path).items():
            if key not in produced:
                orphans.append(f"{path.relative_to(SRC).as_posix()}:{line} reads extra[{key!r}]")

    assert not orphans, "these reads can only ever answer None:\n  " + "\n  ".join(sorted(orphans))


def test_the_allow_list_does_not_outlive_its_reason():
    """A key that gained a writer must leave the list, or it hides the next one."""
    produced = _device_status_fields() | _written_extra_keys()
    stale = sorted(KNOWN_ABSENT & produced)

    assert not stale, f"these are written now and no longer need listing: {stale}"
