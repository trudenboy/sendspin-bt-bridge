"""No coroutine calls the blocking facade.

`BluetoothDevice.*_blocking()` hands its work to the bridge loop and waits for
it. From inside that loop the wait is a deadlock. The module says so at
runtime, but only on the machine that runs that branch — this says so on CI,
for every branch, before anyone ships it.

The same shape as the rule that keeps the Bluetooth monitor out of the
manager's private attributes: a small, mechanical fact about the code, checked
where facts are cheap.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).parents[3] / "src" / "sendspin_bridge"


def _own_body(node: ast.AST):
    """Walk a function's own body, not the bodies nested inside it.

    A `def` written inside a coroutine runs wherever it is later called —
    an executor, a callback — so it is not part of this coroutine's body.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        stack.extend(ast.iter_child_nodes(child))


def _blocking_calls_in_coroutines(tree: ast.AST) -> list[tuple[str, int]]:
    """`(name, line)` for every `*_blocking(...)` call inside an `async def`."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for inner in _own_body(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr.endswith("_blocking")
            ):
                found.append((inner.func.attr, inner.lineno))
    return found


def test_no_coroutine_waits_on_the_bridge_loop_from_inside_it():
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, line in _blocking_calls_in_coroutines(tree):
            offenders.append(f"{path.relative_to(SRC).as_posix()}:{line} calls {name}() from a coroutine")

    assert not offenders, "await the async method instead:\n  " + "\n  ".join(offenders)


def test_the_rule_can_see_an_offender():
    """A rule that cannot fail is a rule that proves nothing."""
    source = """
async def read_it(device):
    return device.is_connected_blocking()
"""
    assert _blocking_calls_in_coroutines(ast.parse(source)) == [("is_connected_blocking", 3)]


def test_a_plain_function_inside_a_coroutine_is_not_an_offender():
    """Executor callbacks are defined there but do not run there."""
    source = """
async def read_it(device, loop):
    def _in_a_thread():
        return device.is_connected_blocking()

    return await loop.run_in_executor(None, _in_a_thread)
"""
    assert _blocking_calls_in_coroutines(ast.parse(source)) == []
