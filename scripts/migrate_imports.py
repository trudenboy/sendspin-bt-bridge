"""One-shot helper to rewrite top-level / services / routes imports for src-layout.

Used during B2–B6 of the structure migration. After Group B is complete, this
script can be deleted (it has no production role).

Usage:
    python scripts/migrate_imports.py --dry-run    # print diff summary
    python scripts/migrate_imports.py              # apply rewrites in place

The mapping is encoded inline (MAPPING below). Both `from <old> import ...` and
`import <old>` (and `import <old> as <alias>`) are handled. A separate pass
catches `importlib.import_module("<old>...")` string forms.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Order matters: longer prefixes must come first so that
# `services.bluetooth.X` is rewritten before `services.bluetooth` matches
# the bare-import line.
MAPPING: list[tuple[str, str]] = [
    # — services subpackage assignments —
    # bluetooth
    ("services.adapter_names", "sendspin_bridge.services.bluetooth.adapter_names"),
    ("services.adapter_recovery", "sendspin_bridge.services.bluetooth.adapter_recovery"),
    ("services.bt_class_of_device", "sendspin_bridge.services.bluetooth.bt_class_of_device"),
    ("services.bt_commands", "sendspin_bridge.services.bluetooth.bt_commands"),
    ("services.bt_operation_lock", "sendspin_bridge.services.bluetooth.bt_operation_lock"),
    ("services.bt_rssi_mgmt", "sendspin_bridge.services.bluetooth.bt_rssi_mgmt"),
    ("services.pairing_agent", "sendspin_bridge.services.bluetooth.pairing_agent"),
    ("services.pairing_quiesce", "sendspin_bridge.services.bluetooth.pairing_quiesce"),
    ("services.device_registry", "sendspin_bridge.services.bluetooth.device_registry"),
    ("services.device_health_state", "sendspin_bridge.services.bluetooth.device_health_state"),
    ("services.device_activation", "sendspin_bridge.services.bluetooth.device_activation"),
    ("services.duplicate_device_check", "sendspin_bridge.services.bluetooth.duplicate_device_check"),
    ("services.hci_avrcp_monitor", "sendspin_bridge.services.bluetooth.hci_avrcp_monitor"),
    ("services.avrcp_source_tracker", "sendspin_bridge.services.bluetooth.avrcp_source_tracker"),
    # audio
    ("services.pa_volume_controller", "sendspin_bridge.services.audio.pa_volume_controller"),
    ("services.pulse", "sendspin_bridge.services.audio.pulse"),
    ("services.mpris_player", "sendspin_bridge.services.audio.mpris_player"),
    ("services.sink_monitor", "sendspin_bridge.services.audio.sink_monitor"),
    ("services.playback_health", "sendspin_bridge.services.audio.playback_health"),
    # music_assistant
    ("services.ma_artwork", "sendspin_bridge.services.music_assistant.ma_artwork"),
    ("services.ma_client", "sendspin_bridge.services.music_assistant.ma_client"),
    ("services.ma_discovery", "sendspin_bridge.services.music_assistant.ma_discovery"),
    ("services.ma_integration_service", "sendspin_bridge.services.music_assistant.ma_integration_service"),
    ("services.ma_monitor", "sendspin_bridge.services.music_assistant.ma_monitor"),
    ("services.ma_runtime_state", "sendspin_bridge.services.music_assistant.ma_runtime_state"),
    # ha
    ("services.ha_addon", "sendspin_bridge.services.ha.ha_addon"),
    ("services.ha_command_dispatcher", "sendspin_bridge.services.ha.ha_command_dispatcher"),
    ("services.ha_core_api", "sendspin_bridge.services.ha.ha_core_api"),
    ("services.ha_entity_model", "sendspin_bridge.services.ha.ha_entity_model"),
    ("services.ha_integration_lifecycle", "sendspin_bridge.services.ha.ha_integration_lifecycle"),
    ("services.ha_mqtt_publisher", "sendspin_bridge.services.ha.ha_mqtt_publisher"),
    ("services.ha_state_projector", "sendspin_bridge.services.ha.ha_state_projector"),
    # ipc
    ("services.ipc_protocol", "sendspin_bridge.services.ipc.ipc_protocol"),
    ("services.bridge_daemon", "sendspin_bridge.services.ipc.bridge_daemon"),
    ("services.bridge_mdns", "sendspin_bridge.services.ipc.bridge_mdns"),
    ("services.bridge_state_model", "sendspin_bridge.services.ipc.bridge_state_model"),
    ("services.daemon_process", "sendspin_bridge.services.ipc.daemon_process"),
    ("services.subprocess_command", "sendspin_bridge.services.ipc.subprocess_command"),
    ("services.subprocess_ipc", "sendspin_bridge.services.ipc.subprocess_ipc"),
    ("services.subprocess_stderr", "sendspin_bridge.services.ipc.subprocess_stderr"),
    ("services.subprocess_stop", "sendspin_bridge.services.ipc.subprocess_stop"),
    # lifecycle
    ("services.lifecycle_state", "sendspin_bridge.services.lifecycle.lifecycle_state"),
    ("services.bridge_runtime_state", "sendspin_bridge.services.lifecycle.bridge_runtime_state"),
    ("services.async_job_state", "sendspin_bridge.services.lifecycle.async_job_state"),
    ("services.reconfig_orchestrator", "sendspin_bridge.services.lifecycle.reconfig_orchestrator"),
    ("services.status_event_builder", "sendspin_bridge.services.lifecycle.status_event_builder"),
    ("services.status_snapshot", "sendspin_bridge.services.lifecycle.status_snapshot"),
    # diagnostics
    ("services.auth_tokens", "sendspin_bridge.services.diagnostics.auth_tokens"),
    ("services.event_hooks", "sendspin_bridge.services.diagnostics.event_hooks"),
    ("services.github_issue_proxy", "sendspin_bridge.services.diagnostics.github_issue_proxy"),
    ("services.guidance_issue_registry", "sendspin_bridge.services.diagnostics.guidance_issue_registry"),
    ("services.internal_events", "sendspin_bridge.services.diagnostics.internal_events"),
    ("services.log_analysis", "sendspin_bridge.services.diagnostics.log_analysis"),
    ("services.onboarding_assistant", "sendspin_bridge.services.diagnostics.onboarding_assistant"),
    ("services.operator_check_runner", "sendspin_bridge.services.diagnostics.operator_check_runner"),
    ("services.operator_guidance", "sendspin_bridge.services.diagnostics.operator_guidance"),
    ("services.preflight_status", "sendspin_bridge.services.diagnostics.preflight_status"),
    ("services.recovery_assistant", "sendspin_bridge.services.diagnostics.recovery_assistant"),
    ("services.recovery_timeline", "sendspin_bridge.services.diagnostics.recovery_timeline"),
    ("services.sendspin_compat", "sendspin_bridge.services.diagnostics.sendspin_compat"),
    ("services.sendspin_port_probe", "sendspin_bridge.services.diagnostics.sendspin_port_probe"),
    ("services.update_checker", "sendspin_bridge.services.diagnostics.update_checker"),
    # infrastructure
    ("services._helpers", "sendspin_bridge.services.infrastructure._helpers"),
    ("services.config_diff", "sendspin_bridge.services.infrastructure.config_diff"),
    ("services.config_validation", "sendspin_bridge.services.infrastructure.config_validation"),
    ("services.port_bind_probe", "sendspin_bridge.services.infrastructure.port_bind_probe"),
    ("services.url_safety", "sendspin_bridge.services.infrastructure.url_safety"),
    # bluetooth: services.bluetooth (the top-level module) becomes the package __init__
    # (handled last so longer matches above run first)
    ("services.bluetooth", "sendspin_bridge.services.bluetooth"),
    # — config modules (B4) —
    # Note: B4 handles these; included here so a single migrate_imports.py pass
    # can be re-run after each step.
    ("config_auth", "sendspin_bridge.config.auth"),
    ("config_migration", "sendspin_bridge.config.migration"),
    ("config_network", "sendspin_bridge.config.network"),
    # — bridge / bluetooth / web core (B5) —
    ("bt_types", "sendspin_bridge.bridge.types"),
    ("exceptions", "sendspin_bridge.bridge.exceptions"),
    ("state", "sendspin_bridge.bridge.state"),
    ("bt_dbus", "sendspin_bridge.bluetooth.dbus"),
    ("bt_audio", "sendspin_bridge.bluetooth.audio"),
    ("bt_monitor", "sendspin_bridge.bluetooth.monitor"),
    ("bluetooth_manager", "sendspin_bridge.bluetooth.manager"),
    ("web_interface", "sendspin_bridge.web.interface"),
    ("bridge_orchestrator", "sendspin_bridge.bridge.orchestrator"),
    # — entry-point (B6) —
    ("sendspin_client", "sendspin_bridge.bridge.client"),
    # — routes (B3) —
    # routes.X → sendspin_bridge.web.routes.X (the routes package itself moves wholesale)
    ("routes.api_bt", "sendspin_bridge.web.routes.api_bt"),
    ("routes.api_config", "sendspin_bridge.web.routes.api_config"),
    ("routes.api_ha", "sendspin_bridge.web.routes.api_ha"),
    ("routes.api_ma", "sendspin_bridge.web.routes.api_ma"),
    ("routes.api_status", "sendspin_bridge.web.routes.api_status"),
    ("routes.api_transport", "sendspin_bridge.web.routes.api_transport"),
    ("routes.api_ws", "sendspin_bridge.web.routes.api_ws"),
    ("routes.api", "sendspin_bridge.web.routes.api"),
    ("routes.auth", "sendspin_bridge.web.routes.auth"),
    ("routes.ma_auth", "sendspin_bridge.web.routes.ma_auth"),
    ("routes.ma_groups", "sendspin_bridge.web.routes.ma_groups"),
    ("routes.ma_playback", "sendspin_bridge.web.routes.ma_playback"),
    ("routes.views", "sendspin_bridge.web.routes.views"),
    ("routes._helpers", "sendspin_bridge.web.routes._helpers"),
    ("routes", "sendspin_bridge.web.routes"),
    # — config — last because shortest prefix —
    ("config", "sendspin_bridge.config"),
]


def _replacement_patterns(old: str, new: str) -> list[tuple[re.Pattern[str], str]]:
    old_re = re.escape(old)
    patterns = [
        # `from <old> import ...` (covers multi-line and submodule)
        (re.compile(rf"^(\s*from\s+){old_re}(\s+import\s)", re.MULTILINE), rf"\1{new}\2"),
        # `import <old>.X` (with submodule)
        (re.compile(rf"^(\s*import\s+){old_re}(\.[A-Za-z_])", re.MULTILINE), rf"\1{new}\2"),
        # `import <old> as alias`
        (re.compile(rf"^(\s*import\s+){old_re}(\s+as\s)", re.MULTILINE), rf"\1{new}\2"),
        # `import <old>` (bare, end of line)
        (re.compile(rf"^(\s*import\s+){old_re}(\s*$)", re.MULTILINE), rf"\1{new}\2"),
        # `importlib.import_module("<old>...")` and `__import__("<old>...")`
        (re.compile(rf'(import_module\(\s*["\']){old_re}(["\.])'), rf"\1{new}\2"),
        (re.compile(rf'(__import__\(\s*["\']){old_re}(["\.])'), rf"\1{new}\2"),
        # Any quoted module path: `"<old>.X"` or `"<old>"` exactly. Used by
        # @patch("...") / monkeypatch.setattr("...") / logger names. Conservative:
        # only matches when the next char is `.`, `"` or `'` (avoiding false matches
        # against words that just happen to start with the old prefix).
        (re.compile(rf'(["\']){old_re}([\."\'])'), rf"\1{new}\2"),
    ]

    # Special case: when old is `services.X` (a leaf submodule of services), also rewrite
    # `from services import X` (and `from services import X as Y`) to
    # `from sendspin_bridge.services.<subpkg> import X (as Y)`.
    # This pattern was implicitly used pre-migration to access submodules via the parent package.
    if "." in old and old.startswith(("services.", "config")):
        parent, _, leaf = new.rpartition(".")
        # Match: `from <pkg> import LEAF` (optionally `as ALIAS`); single-import-per-line only.
        if old.startswith("services."):
            pkg = "services"
        elif old == "config_auth":
            pkg = "config"  # historical: config_auth was a top-level module, no parent rewrite needed
            return patterns
        else:
            return patterns
        leaf_re = re.escape(leaf)
        patterns.append(
            (
                re.compile(rf"^(\s*from\s+){pkg}(\s+import\s+){leaf_re}(\s+as\s+\w+)?(\s*$)", re.MULTILINE),
                rf"\1{parent}\2{leaf}\3\4",
            )
        )
    return patterns


def _iter_target_files() -> Iterable[Path]:
    excluded_dirs = {
        ".git",
        ".venv",
        ".worktrees",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".scratch",
        "img",
        "sendspin-cli",
        "node_modules",
        "docs-site",
        "ha-addon-assets",
    }
    for path in _REPO_ROOT.rglob("*.py"):
        if any(part in excluded_dirs for part in path.parts):
            continue
        # Skip the helper script itself (would rewrite our mapping table).
        if path.name == "migrate_imports.py":
            continue
        yield path


def _apply_to_text(text: str) -> tuple[str, int]:
    total = 0
    new_text = text
    for old, new in MAPPING:
        for pattern, replacement in _replacement_patterns(old, new):
            new_text, n = pattern.subn(replacement, new_text)
            total += n
    return new_text, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print summary; don't modify files")
    parser.add_argument(
        "--paths",
        nargs="*",
        type=Path,
        default=None,
        help="Restrict rewrite to these paths (default: all *.py in repo)",
    )
    parser.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="Only apply MAPPING entries whose OLD name starts with this prefix. "
        "Repeat to allow multiple prefixes. Useful for per-step migrations "
        "(e.g. --prefix services. for B2).",
    )
    args = parser.parse_args()

    if args.prefix:
        global MAPPING
        MAPPING = [(old, new) for (old, new) in MAPPING if any(old.startswith(p) for p in args.prefix)]
        print(f"Filtered mapping to {len(MAPPING)} rules with prefix(es): {args.prefix}")

    targets = [p.resolve() for p in args.paths] if args.paths else list(_iter_target_files())
    grand_total = 0
    files_touched = 0
    for path in targets:
        try:
            original = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        new_text, count = _apply_to_text(original)
        if count == 0:
            continue
        files_touched += 1
        grand_total += count
        rel = path.relative_to(_REPO_ROOT)
        if args.dry_run:
            print(f"  {rel}: {count} replacement(s)")
        else:
            path.write_text(new_text)
            print(f"  {rel}: {count} replacement(s) applied")

    print(f"\nTotal: {grand_total} replacement(s) across {files_touched} file(s){' (dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
