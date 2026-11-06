"""The bridge's read model: one build of the health story every surface reports.

The header pill (``/status``), the guidance, onboarding and recovery
endpoints, the diagnostics bundle and the bug report all describe the same
bridge.  Each of them used to run the chain *state model → onboarding →
recovery → guidance → verdict* itself, and each passed it slightly different
inputs — so the same bridge read "All devices disabled" in the header and
"Waiting for setup" in its diagnostics bundle.

Here the chain is written once.  A caller gathers :class:`ReadInputs` with
:func:`inputs_now` and asks :func:`build` for a :class:`ReadView`; every part
of the view is derived from those inputs and from each other, and only the
parts a caller reads are computed.

The one legitimate difference between surfaces is how fresh the host probe
is: the status tick reads the sampled preflight, a diagnostics bundle
measures the host now (``fresh_preflight=True``).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.diagnostics.status_model import Verdict, verdict_from_issues
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from sendspin_bridge.services.lifecycle.status_snapshot import BridgeSnapshot, build_bridge_snapshot

if TYPE_CHECKING:
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
    from sendspin_bridge.services.ipc.bridge_state_model import BridgeStateModel

__all__ = ["ReadInputs", "ReadView", "build", "inputs_now"]


@dataclass(frozen=True)
class ReadInputs:
    """Everything the read model is derived from: the config, the host probe and the runtime snapshot."""

    config: dict[str, Any]
    preflight: dict[str, Any]
    snapshot: BridgeSnapshot

    @property
    def devices(self) -> list[Any]:
        return self.snapshot.devices

    @property
    def disabled_devices(self) -> list[dict[str, Any]]:
        return self.snapshot.disabled_devices

    @property
    def startup_progress(self) -> dict[str, Any]:
        progress = self.snapshot.startup_progress
        return progress.to_dict() if progress else {}


class ReadView:
    """The bridge's state model, onboarding, recovery, guidance and verdict for one :class:`ReadInputs`.

    Parts are computed on first read and kept: a surface that renders only the
    recovery card never builds the guidance, and a surface that renders all of
    them builds each once, from the same state model.
    """

    def __init__(self, inputs: ReadInputs) -> None:
        self.inputs = inputs

    @cached_property
    def bridge_state(self) -> BridgeStateModel:
        i = self.inputs
        return build_bridge_state_model(
            config=i.config,
            preflight=i.preflight,
            devices=i.devices,
            ma_connected=i.snapshot.ma_connected,
            runtime_mode=i.snapshot.runtime_mode,
            startup_progress=i.startup_progress,
            update_available=bool(i.snapshot.update_available),
            disabled_devices=i.disabled_devices,
        )

    @cached_property
    def onboarding(self) -> dict[str, Any]:
        i = self.inputs
        return _builders()._build_onboarding_assistant_payload(
            preflight=i.preflight,
            config=i.config,
            devices=i.devices,
            runtime_mode=i.snapshot.runtime_mode,
            ma_connected=i.snapshot.ma_connected,
            bridge_state=self.bridge_state,
        )

    @cached_property
    def recovery(self) -> dict[str, Any]:
        i = self.inputs
        return _builders()._build_recovery_assistant_payload(
            preflight=i.preflight,
            config=i.config,
            devices=i.devices,
            onboarding_assistant=self.onboarding,
            startup_progress=i.startup_progress,
            bridge_state=self.bridge_state,
        )

    @cached_property
    def guidance(self) -> dict[str, Any]:
        i = self.inputs
        return _builders()._build_operator_guidance_payload(
            config=i.config,
            devices=i.devices,
            disabled_devices=i.disabled_devices,
            onboarding_assistant=self.onboarding,
            recovery_assistant=self.recovery,
            startup_progress=i.startup_progress,
            preflight=i.preflight,
            runtime_mode=i.snapshot.runtime_mode,
            ma_connected=i.snapshot.ma_connected,
            bridge_state=self.bridge_state,
        )

    @property
    def verdict(self) -> Verdict:
        """The worst issue in the guidance — what the header pill and the Diagnostics verdict both show."""
        return verdict_from_issues(self.guidance.get("issue_groups") or [])


def build(inputs: ReadInputs) -> ReadView:
    """The read model for *inputs*."""
    return ReadView(inputs)


def inputs_now(
    *,
    registry: DeviceRegistrySnapshot | None = None,
    config: dict[str, Any] | None = None,
    fresh_preflight: bool = False,
) -> ReadInputs:
    """Gather the inputs from the running bridge.

    *registry* and *config* may be supplied by a caller that has already read
    them; otherwise they are read here.  The host probe is the sampled one
    unless *fresh_preflight* asks for a measurement now.
    """
    diag = _builders()
    if registry is None:
        registry = diag.get_device_registry_snapshot()
    if config is None:
        config = diag.load_config()
    preflight = diag._collect_preflight_status() if fresh_preflight else diag._sampled_preflight_status()
    return ReadInputs(config=config, preflight=preflight, snapshot=build_bridge_snapshot(registry.active_clients))


def _builders():
    """The step builders and host readers, looked up at call time.

    They live with the diagnostics use cases, which import this module; the
    late lookup keeps the import acyclic and lets a test replace one step.
    """
    from sendspin_bridge.application import diagnostics

    return diagnostics
