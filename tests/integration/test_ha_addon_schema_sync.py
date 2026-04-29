"""Regression: every option pushed to Supervisor must be in the addon schema.

When ``routes.api_config._sync_ha_options`` POSTs an option key that is
not declared in ``ha-addon*/config.yaml`` (under both ``options:`` and
``schema:``), Home Assistant Supervisor silently strips it.  On the next
addon restart ``scripts/translate_ha_config.py`` reads ``options.json``
back and sees the missing key as ``False``/empty default — so the user
loses the setting they just saved (e.g. ``Disable PA rescue-streams``
toggling itself off after every restart).

This test parses the YAML files with a hand-rolled top-level scanner
(no PyYAML dependency, since the bridge runtime doesn't ship it) and
asserts every Supervisor-bound key from ``_sync_ha_options`` is present
in all three addon configs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Keys ``_sync_ha_options`` always pushes to Supervisor.  Keep this list
# in sync with ``routes/api_config.py:_sync_ha_options``; the test below
# checks every entry against every addon config.
ALWAYS_PUSHED_KEYS: tuple[str, ...] = (
    "sendspin_server",
    "sendspin_port",
    "bridge_name",
    "ha_area_name_assist_enabled",
    "tz",
    "pulse_latency_msec",
    "startup_banner_grace_seconds",
    "recovery_banner_grace_seconds",
    "prefer_sbc_codec",
    "disable_pa_rescue_streams",  # the one that broke in #user-report
    "bt_check_interval",
    "bt_max_reconnect_fails",
    "ma_auto_silent_auth",
    "bluetooth_devices",
    "bluetooth_adapters",
)

# These keys are in ``_sync_ha_options`` but intentionally absent from
# the schema because translate_ha_config doesn't read them back (the
# HA-addon runtime hardcodes the underlying behaviour, e.g. AUTH_ENABLED
# is always-on under HA addon mode).  Listed here so the diff against
# ``_sync_ha_options`` is explicit and reviewable.
INTENTIONALLY_NOT_IN_SCHEMA: tuple[str, ...] = ("auth_enabled",)

ADDON_CONFIG_FILES: tuple[Path, ...] = (
    REPO_ROOT / "ha-addon" / "config.yaml",
    REPO_ROOT / "ha-addon-rc" / "config.yaml",
    REPO_ROOT / "ha-addon-beta" / "config.yaml",
)


def _read_section_keys(yaml_path: Path, section: str) -> set[str]:
    """Return the top-level keys nested under ``section:`` in *yaml_path*.

    Hand-rolled scanner because the runtime venv doesn't ship PyYAML.
    Recognises only what the addon configs use today: a top-level
    ``section:`` line followed by 2-space-indented ``key: value`` or
    ``key:`` (list/dict opener) lines.  Stops as soon as it hits the
    next top-level (column-0) non-blank, non-comment line.
    """
    text = yaml_path.read_text()
    keys: set[str] = set()
    in_section = False
    section_re = re.compile(rf"^{re.escape(section)}\s*:\s*$")
    nested_re = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*)\s*:")
    for raw in text.splitlines():
        if section_re.match(raw):
            in_section = True
            continue
        if not in_section:
            continue
        # End of section: any non-blank, non-comment line at column 0.
        stripped = raw.rstrip()
        if stripped and not stripped.startswith(" ") and not stripped.startswith("#"):
            break
        m = nested_re.match(raw)
        if m:
            keys.add(m.group(1))
    return keys


@pytest.mark.parametrize("addon_config", ADDON_CONFIG_FILES, ids=lambda p: p.parent.name)
def test_addon_config_options_section_includes_every_pushed_key(addon_config):
    options_keys = _read_section_keys(addon_config, "options")
    missing = [k for k in ALWAYS_PUSHED_KEYS if k not in options_keys]
    assert not missing, (
        f"{addon_config.relative_to(REPO_ROOT)} options: missing default for keys "
        f"{missing} that _sync_ha_options POSTs.  Supervisor strips unknown options "
        f"on save; users see the setting reset to False/empty on every restart."
    )


@pytest.mark.parametrize("addon_config", ADDON_CONFIG_FILES, ids=lambda p: p.parent.name)
def test_addon_config_schema_section_includes_every_pushed_key(addon_config):
    schema_keys = _read_section_keys(addon_config, "schema")
    missing = [k for k in ALWAYS_PUSHED_KEYS if k not in schema_keys]
    assert not missing, (
        f"{addon_config.relative_to(REPO_ROOT)} schema: missing type for keys "
        f"{missing}.  Supervisor validates options against the schema and silently "
        f"drops any key not declared here."
    )


@pytest.mark.parametrize("addon_config", ADDON_CONFIG_FILES, ids=lambda p: p.parent.name)
def test_intentionally_unmapped_keys_are_absent_from_schema(addon_config):
    # Sanity check the documented exemptions list — if someone adds
    # ``auth_enabled`` to the schema in future, this test fires and the
    # exemption needs revisiting (and possibly a translate_ha_config
    # entry to read it back).
    schema_keys = _read_section_keys(addon_config, "schema")
    leaked = [k for k in INTENTIONALLY_NOT_IN_SCHEMA if k in schema_keys]
    assert not leaked, (
        f"{addon_config.relative_to(REPO_ROOT)} schema includes {leaked} which "
        f"INTENTIONALLY_NOT_IN_SCHEMA says should be absent.  Either add a "
        f"translate_ha_config entry that reads it back or remove from the schema."
    )
