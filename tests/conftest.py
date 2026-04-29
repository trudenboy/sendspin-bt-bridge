"""Shared test fixtures for sendspin-bt-bridge."""

import pytest


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
def _fast_bt_pair_timing(monkeypatch, request):
    """Shrink BT pair/scan wait constants and short-circuit time.sleep in BT modules.

    The pair/scan/reset paths use real `time.sleep(N)` calls (12s scan-discover,
    10/15s wait-for-pair, 1-15s adapter power cycle). Tests already mock the
    underlying `subprocess.Popen` / `selectors.DefaultSelector`, so the sleeps
    are pure waste in CI — they account for ~110s of the 191s full-suite run.

    This autouse fixture:
      - Drops `_PAIRING_SCAN_DURATION` / `_PAIRING_WAIT_DURATION` (manager.py)
        and `_PAIR_SCAN_DURATION` / `_PAIR_WAIT_DURATION` (api_bt.py) to 0.1s
        so deadline-bounded loops still iterate at least once but exit fast.
      - Replaces `time.sleep` inside those two modules with a no-op for any
        wait ≥ 0.5s; shorter sleeps (e.g. 0.2s polling cadence) pass through
        in case a test relies on cooperative yielding.
    """
    # Opt-out for tests that exercise the timing/agent code itself.
    test_path = str(getattr(request, "path", ""))
    test_name = request.node.name
    if "test_pairing_agent.py" in test_path:
        return
    if "without_fixed_scan_delay" in test_name:
        # Compares actual sleep durations against _PAIR_SCAN_DURATION;
        # patching the constant skews the comparison.
        return

    import time as _time

    try:
        import sendspin_bridge.bluetooth.manager as _mgr
        import sendspin_bridge.web.routes.api_bt as _api_bt
    except ImportError:
        # test_web_interface.py stubs sendspin_bridge.web.routes.api_bt
        # at module-collection time with `types.ModuleType`. Subsequent
        # imports return the stub which has no real submodules / attrs.
        # Skip the optimization in that environment — those tests don't
        # exercise the pair / scan timing paths anyway.
        return

    # If api_bt is a stub (test_web_interface monkey-patches it),
    # `_PAIR_SCAN_DURATION` won't exist; skip.
    if not hasattr(_api_bt, "_PAIR_SCAN_DURATION"):
        return

    # 1. Shrink pair-flow durations from seconds to centiseconds.
    monkeypatch.setattr(_mgr, "_PAIRING_SCAN_DURATION", 0.1, raising=False)
    monkeypatch.setattr(_mgr, "_PAIRING_WAIT_DURATION", 0.1, raising=False)
    monkeypatch.setattr(_api_bt, "_PAIR_SCAN_DURATION", 0.1, raising=False)
    monkeypatch.setattr(_api_bt, "_PAIR_WAIT_DURATION", 0.1, raising=False)

    # 2. Skip long literal sleeps inside mocked subprocess flows.
    _real_sleep = _time.sleep

    def _fast_sleep(seconds):
        # Pass through short cooperative sleeps (e.g. polling cadence,
        # `_wait_with_cancel` step=0.2). Long waits (≥0.5s) are mock waste.
        if seconds < 0.5:
            _real_sleep(seconds)

    monkeypatch.setattr(_mgr.time, "sleep", _fast_sleep, raising=False)
    monkeypatch.setattr(_api_bt.time, "sleep", _fast_sleep, raising=False)

    # 3. Short-circuit PairingAgent's D-Bus connect dance.
    # On hosts without dbus_fast / SystemBus (macOS, plain CI containers),
    # `__enter__` waits up to 5s for `_ready.wait`, then a 5s thread join
    # on `_start_error` — accounting for ~5s per pair_device test (the
    # manager catches the RuntimeError and falls back to bluetoothctl
    # agent anyway). Force the immediate-fail path.
    import sendspin_bridge.services.bluetooth.pairing_agent as _pair_agent

    def _fast_enter(self):
        raise RuntimeError("PairingAgent disabled in tests (use bluetoothctl agent fallback)")

    monkeypatch.setattr(_pair_agent.PairingAgent, "__enter__", _fast_enter, raising=False)


@pytest.fixture(autouse=True)
def _reset_shared_module_state():
    """Reset known process-wide module-level state between tests.

    pytest-xdist's `loadfile` distribution puts whole files into one
    worker, but unrelated files can still share a worker — and when one
    file leaks shared state (BT operation lock, MA API credentials,
    scan-job dict), the next file's tests see stale values.

    Reset the few known leak sites BEFORE each test. Cheap; deterministic.
    """
    # 1. Force-release the global BT operation lock. Some test failure
    #    paths (e.g. pair flow exception before _release_bt_operation)
    #    leave the threading.Lock acquired.
    import sendspin_bridge.services.bluetooth.bt_operation_lock as _btlock

    try:
        _btlock._bt_operation_lock.release()
    except RuntimeError:
        pass  # already released — expected

    # 2. Clear MA API credentials so `_build_imageproxy_url` returns the
    #    raw path instead of a fully-qualified `http://ma:8095/imageproxy`
    #    URL when the test didn't explicitly opt in.
    import sendspin_bridge.bridge.state as _state

    _state.set_ma_api_credentials("", "")

    # 3. Clear in-flight scan jobs so /api/bt/scan doesn't 409 with
    #    "scan already in progress" from a sibling test.
    import sendspin_bridge.services.lifecycle.async_job_state as _ajs

    with _ajs._scan_jobs_lock:
        _ajs._scan_jobs.clear()

    yield
