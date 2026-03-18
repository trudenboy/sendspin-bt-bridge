from scripts.check_container_runtime import _import_runtime_modules, _run_translation_smoke


def test_container_runtime_smoke_imports_runtime_modules():
    imported = _import_runtime_modules()

    assert "bridge_orchestrator" in imported
    assert "sendspin_client" in imported


def test_container_runtime_smoke_runs_translation_path():
    translated = _run_translation_smoke()

    assert translated["UPDATE_CHANNEL"] == "rc"
    assert translated["BLUETOOTH_DEVICES"][0]["enabled"] is True
