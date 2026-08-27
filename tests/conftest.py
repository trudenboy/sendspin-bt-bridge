"""Shared test fixtures for sendspin-bt-bridge."""

import pytest


@pytest.fixture(autouse=True)
def _unsampled_host_probe():
    """Each test measures the host for itself.

    The status path samples the host probe at a bounded rate through a
    process-wide cache, so without this a probe stubbed by one test would be
    served to the next one that builds a status payload.
    """
    from sendspin_bridge.web.routes.api_status import reset_preflight_probe

    reset_preflight_probe()
    yield
    reset_preflight_probe()


@pytest.fixture(scope="session")
def _bridge_loop_thread():
    """One background event loop for the whole session.

    Work that crosses to the bridge loop — the device module's blocking
    reads, the controller's verbs over D-Bus — answers "don't know" without
    one, which is right in production and useless in a test that wants an
    answer.  Registering it is left to whoever asks for ``bridge_loop``.
    """
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="test-bridge-loop")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


@pytest.fixture
def bridge_loop(_bridge_loop_thread):
    """Register the session loop as the bridge loop, and put it back after."""
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as runtime

    previous = runtime.get_main_loop()
    runtime.set_main_loop(_bridge_loop_thread)
    try:
        yield _bridge_loop_thread
    finally:
        runtime.set_main_loop(previous)


@pytest.fixture(autouse=True)
def _no_real_system_bus(monkeypatch):
    """No test reaches the host's own BlueZ.

    The controller's verbs prefer BlueZ's bus and fall back to bluetoothctl
    when it cannot answer.  On a developer machine that bus is real, with
    real controllers and a real speaker behind it — a test that means to
    exercise the fallback would power an adapter instead.  Tests that want
    the D-Bus path hand the controller a bus of their own.
    """
    from sendspin_bridge.bluetooth import controller as controller_mod

    def _no_bus():
        raise ConnectionError("the system bus is not available to tests")

    monkeypatch.setattr(controller_mod, "_system_bus", _no_bus)
    controller_mod.set_controller(None)
    yield
    controller_mod.set_controller(None)


@pytest.fixture
def fake_bluez():
    """The shared bluetoothctl fake (tests/support/fake_bluez.py).

    Substitutes the ``Spawner`` protocol of ``BluezControl`` — never
    patches ``subprocess``.  ``fake_bluez.control`` is a ready
    ``BluezControl`` wired to the fake with an instant clock.
    """
    from tests.support.fake_bluez import FakeBluez

    return FakeBluez()


@pytest.fixture
def installed_bluez(fake_bluez):
    """Install the fake as the shared ``get_bluez()`` singleton.

    Call sites migrated to BluezControl resolve the transport through
    ``get_bluez()``; this fixture points the singleton at the fake for
    the duration of the test and resets it afterwards.
    """
    from sendspin_bridge.bluetooth.bluez import set_bluez

    set_bluez(fake_bluez.control)
    try:
        yield fake_bluez
    finally:
        set_bluez(None)


@pytest.fixture
def bluez_sysfs():
    """Install a ``BluezControl`` whose sysfs root is a tmp tree.

    ``BluezControl.hci_map()`` reads its ``sysfs_dir`` (the canonical
    kernel MAC→hciN mapping); tests call ``bluez_sysfs(path)`` to point it
    at a fake ``/sys/class/bluetooth`` and the singleton is reset
    afterwards.  A FakeBluez spawner backs the control so the
    ``hciconfig`` fallback never spawns a real subprocess.
    """
    from sendspin_bridge.bluetooth.bluez import BluezControl, set_bluez
    from tests.support.fake_bluez import FakeBluez

    def _install(sysfs_dir):
        set_bluez(BluezControl(spawner=FakeBluez(), sysfs_dir=sysfs_dir))

    try:
        yield _install
    finally:
        set_bluez(None)


@pytest.fixture
def tmp_config(tmp_path):
    """Provide a temporary config file and set CONFIG_FILE/CONFIG_DIR."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    import sendspin_bridge.config as _cfg

    original_file = _cfg.CONFIG_FILE
    original_dir = _cfg.CONFIG_DIR
    _cfg.CONFIG_FILE = config_file
    _cfg.CONFIG_DIR = tmp_path
    yield config_file
    _cfg.CONFIG_FILE = original_file
    _cfg.CONFIG_DIR = original_dir


@pytest.fixture(autouse=True)
def _reset_shared_module_state():
    """Reset known process-wide module-level state between tests.

    pytest-xdist's `loadfile` distribution puts whole files into one
    worker, but unrelated files can still share a worker — and when one
    file leaks shared state (MA API credentials, scan-job dict), the
    next file's tests see stale values.

    Reset the few known leak sites BEFORE each test. Cheap; deterministic.

    The BT operation lock is deliberately NOT force-released here: after
    the batch-5 lock fixes every entry point releases in ``finally``, so
    a leaked acquire is a real bug — and without a silent release the
    leak surfaces as a visible test failure instead of being swept.
    """
    # 1. Clear MA API credentials so `_build_imageproxy_url` returns the
    #    raw path instead of a fully-qualified `http://ma:8095/imageproxy`
    #    URL when the test didn't explicitly opt in.
    import sendspin_bridge.bridge.state as _state

    _state.set_ma_api_credentials("", "")

    # 2. Clear in-flight scan jobs so /api/bt/scan doesn't 409 with
    #    "scan already in progress" from a sibling test.
    import sendspin_bridge.services.lifecycle.async_job_state as _ajs

    with _ajs._scan_jobs_lock:
        _ajs._scan_jobs.clear()

    # 3. Re-sync the `routes` package's submodule attributes with the
    #    sys.modules entries. test_scan_cooldown's stash/pop fixture
    #    sometimes leaves `routes.api_bt` pointing at a re-imported,
    #    now-popped instance while sys.modules holds the original —
    #    `monkeypatch.setattr(api_bt, ...)` then patches one and
    #    Flask's blueprint references the other.
    import sys as _sys

    import sendspin_bridge.web.routes as _routes_pkg

    for _submod in (
        "api",
        "api_bt",
        "api_config",
        "api_status",
        "api_ha",
        "api_ma",
        "api_transport",
        "api_ws",
        "auth",
        "ma_auth",
        "ma_groups",
        "ma_playback",
        "views",
        "_helpers",
    ):
        _full = f"sendspin_bridge.web.routes.{_submod}"
        if _full in _sys.modules:
            setattr(_routes_pkg, _submod, _sys.modules[_full])

    yield
