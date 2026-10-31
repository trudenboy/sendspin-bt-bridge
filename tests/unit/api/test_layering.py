"""The layering that makes the API an adapter: routers never import services.

The scan covers imports at **any depth** — module level and inside function
bodies — and catches both ``from sendspin_bridge.services...`` and the
``from sendspin_bridge import services`` form.  It does not cover dynamic
imports (``importlib``/``__import__``), and it only looks at ``api/routers``.

Module-level service imports are forbidden in **every** router.
``api/routers/auth.py`` is a deliberate, narrower exception: it still imports a
few diagnostics token services *inside* its handlers, because those modules pull
in the auth runtime and the router must stay importable without it.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge"
ROUTERS = PACKAGE / "api" / "routers"
SERVICES_PREFIX = "sendspin_bridge.services"
#: Routers allowed to keep *function-local* service imports (see the docstring).
FUNCTION_LOCAL_SERVICE_IMPORTS = {"auth.py"}


def _imports(path: Path, *, module_level: bool = False) -> list[str]:
    """Every imported module in *path*, including imports inside function bodies."""
    tree = ast.parse(path.read_text())
    nodes = tree.body if module_level else ast.walk(tree)
    modules: list[str] = []
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            modules.append(module)
            if module == "sendspin_bridge":
                modules.extend(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def _router_files() -> list[Path]:
    return sorted(ROUTERS.glob("*.py"))


def _services_imports(path: Path, *, module_level: bool = False) -> list[str]:
    return [
        module
        for module in _imports(path, module_level=module_level)
        if module == SERVICES_PREFIX or module.startswith(SERVICES_PREFIX + ".")
    ]


def test_there_is_at_least_one_router():
    assert _router_files()


def test_the_scan_sees_a_function_local_import_from_the_package(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("def handler():\n    from sendspin_bridge import services\n")

    assert "sendspin_bridge.services" in _imports(sample)


def test_no_router_imports_services_at_module_level():
    offenders = [
        f"{path.name}: {module}" for path in _router_files() for module in _services_imports(path, module_level=True)
    ]

    assert offenders == []


def test_no_router_imports_services_in_a_function_body_except_the_documented_exception():
    offenders = [
        f"{path.name}: {module}"
        for path in _router_files()
        if path.name not in FUNCTION_LOCAL_SERVICE_IMPORTS
        for module in _services_imports(path)
    ]

    assert offenders == []


def test_application_never_imports_the_api():
    offenders = [
        f"{path.name}: {module}"
        for path in sorted((PACKAGE / "application").rglob("*.py"))
        for module in _imports(path)
        if module == "sendspin_bridge.api" or module.startswith("sendspin_bridge.api.")
    ]

    assert offenders == []
