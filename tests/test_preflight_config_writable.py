"""Tests: ``collect_preflight_status`` includes ``config_writable``.

Issue #190 surfaced as a slow triage because there was no Diagnostics
panel signal pointing at file ownership; preflight only collected
audio / bluetooth / memory / dbus.

After this addition the same Diagnostics page that already shows
"Audio: pulseaudio 17.0 / 4 sinks" gains a row "Config write: ✓
writable" or "✗ permission denied" with the chown remediation —
visible to operators who never read container logs.
"""

from __future__ import annotations

import errno

from services.preflight_status import collect_preflight_status


def _stub_collectors():
    """Stubs for the surrounding collectors so the test focuses on
    the new config-writable check."""
    return {
        "get_server_name_fn": lambda: "PulseAudio 17.0",
        "list_sinks_fn": lambda: [],
        "subprocess_module": _StubSubprocess(),
        "runtime_version_fn": lambda: "test",
        "machine_fn": lambda: "x86_64",
        "exists_fn": lambda _p: False,
        "open_fn": lambda _p: _MemInfoStub(),
    }


class _StubSubprocess:
    @staticmethod
    def run(*_args, **_kwargs):
        class _R:
            stdout = ""

        return _R()


class _MemInfoStub:
    def __enter__(self):
        return iter(["MemTotal:   1024000 kB"])

    def __exit__(self, *_):
        return False


def test_preflight_payload_includes_config_writable_key(tmp_path, monkeypatch):
    """The returned dict must have a top-level ``config_writable``
    entry alongside ``audio`` / ``bluetooth`` / ``dbus`` / ``memory_mb``
    so ``api_status`` can render it without conditional null checks."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    result = collect_preflight_status(**_stub_collectors())

    assert "config_writable" in result
    assert isinstance(result["config_writable"], dict)


def test_preflight_config_writable_ok_for_writable_dir(tmp_path, monkeypatch):
    """Happy path: tmp_path is writable → status ok, writable=True,
    no remediation hint."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    result = collect_preflight_status(**_stub_collectors())

    assert result["config_writable"]["status"] == "ok"
    assert result["config_writable"]["writable"] is True
    assert result["config_writable"].get("remediation") in (None, "")


def test_preflight_config_writable_degraded_with_chown_hint_when_permission_denied(tmp_path, monkeypatch):
    """Issue #190 scenario: dir exists but is not writable for the
    current process.  Status flips to ``degraded``, error code is
    ``permission_denied`` (canonical from ``collection_error_payload``),
    and a ``remediation`` string carries the chown command."""
    import config

    # Make the path read-only for the test process by patching os.access
    # — much more reliable than chmod 555 in CI where the test runner
    # may itself be root and bypass mode bits.
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    import services.preflight_status as preflight_module

    def _fake_writable(_dir):
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr(preflight_module, "_probe_config_writable", _fake_writable)

    result = collect_preflight_status(**_stub_collectors())

    assert result["config_writable"]["status"] == "degraded"
    assert result["config_writable"]["writable"] is False
    assert result["config_writable"]["error"]["code"] == "permission_denied"
    assert "chown" in result["config_writable"].get("remediation", "").lower()
    assert "config_writable" in result["failed_collections"]


def test_preflight_overall_status_flips_to_degraded_on_writable_failure(tmp_path, monkeypatch):
    """If ``config_writable`` is the *only* failed collection, the
    top-level ``status`` must still be ``degraded`` so the existing
    UI banner triggers — operators don't have to drill into the
    payload to know something is wrong."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    import services.preflight_status as preflight_module

    def _fake_writable(_dir):
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr(preflight_module, "_probe_config_writable", _fake_writable)

    result = collect_preflight_status(**_stub_collectors())

    assert result["status"] == "degraded"


def test_preflight_config_writable_records_path_and_uid(tmp_path, monkeypatch):
    """The payload must include the actual ``$CONFIG_DIR`` path and
    the runtime UID so a bug-report attached blob makes the
    misconfiguration self-evident — no need to ask the operator
    "what does ls -la /config show"."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    result = collect_preflight_status(**_stub_collectors())

    assert result["config_writable"]["config_dir"] == str(tmp_path)
    assert isinstance(result["config_writable"]["uid"], int)
