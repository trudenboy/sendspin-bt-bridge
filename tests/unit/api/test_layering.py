"""The layering that makes the API an adapter: routers never import services."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge"
ROUTERS = PACKAGE / "api" / "routers"
SERVICES_PREFIX = "sendspin_bridge.services"


def _module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def _router_files() -> list[Path]:
    return sorted(ROUTERS.glob("*.py"))


def test_there_is_at_least_one_router():
    assert _router_files()


def test_no_router_imports_services_at_module_level():
    offenders = [
        f"{path.name}: {module}"
        for path in _router_files()
        for module in _module_level_imports(path)
        if module == SERVICES_PREFIX or module.startswith(SERVICES_PREFIX + ".")
    ]

    assert offenders == []


def test_application_never_imports_the_api_at_module_level():
    offenders = [
        f"{path.name}: {module}"
        for path in sorted((PACKAGE / "application").rglob("*.py"))
        for module in _module_level_imports(path)
        if module == "sendspin_bridge.api" or module.startswith("sendspin_bridge.api.")
    ]

    assert offenders == []
