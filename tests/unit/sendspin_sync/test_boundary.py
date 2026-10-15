"""The core must stay portable: nothing from the bridge may be imported."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[3] / "src" / "sendspin_sync"


def test_core_never_imports_the_bridge():
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.startswith("sendspin_bridge")]
    assert offenders == []


def test_package_imports():
    import sendspin_sync

    assert sendspin_sync.__all__
