"""``/adapters`` and ``/bluetooth`` — controllers, discovery, pairing, the BlueZ cache."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, StrictBool

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import bluetooth as bt
from sendspin_bridge.application.jobs import Job

router = APIRouter(tags=["bluetooth"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


class AdapterOut(BaseModel):
    id: str = Field(description="Kernel name, e.g. hci0")
    mac: str
    name: str
    powered: bool | None = None
    live_class: str | None = Field(default=None, description="Class of Device as read from the controller.")


class PowerIn(BaseModel):
    on: bool


class PowerOut(BaseModel):
    applied: bool


class KnownDevice(BaseModel):
    mac: str
    name: str
    adapters: list[str] = Field(default_factory=list)


class ScanIn(BaseModel):
    adapter: str = Field(description="hciN or controller MAC")
    audio_only: bool = True


# Risky options are strict booleans: a truthy string such as "false" must never
# switch on Just-Works pairing or hands-free authorization.


class PairingIn(BaseModel):
    mac: str
    adapter: str = ""
    quiesce_adapter: StrictBool = False
    no_input_no_output_agent: StrictBool = Field(
        default=False, description="Just-Works SSP for speakers that cancel a passkey (#168)."
    )
    allow_hfp_profile: StrictBool = Field(
        default=False, description="Authorize the hands-free profile for this attempt."
    )


class ResetIn(BaseModel):
    mac: str
    adapter: str = ""
    no_input_no_output_agent: StrictBool = False
    allow_hfp_profile: StrictBool = False


@router.get("/adapters", response_model=list[AdapterOut], summary="Bluetooth controllers")
def list_adapters() -> list[AdapterOut]:
    return [AdapterOut(**a) for a in bt.list_adapters()]


@router.put("/adapters/{adapter_id}/power", response_model=PowerOut, summary="Power a controller on or off")
def put_power(adapter_id: str, body: PowerIn) -> PowerOut:
    return PowerOut(applied=bt.set_adapter_power(adapter_id, body.on))


@router.post("/bluetooth/scans", status_code=202, response_model=Job, summary="Discover nearby devices")
def start_scan(body: ScanIn) -> Job:
    """About 15 s. The job result is ``{"devices": [...], "stats": {...}}``."""
    return bt.start_scan(body.adapter, audio_only=body.audio_only)


@router.post("/bluetooth/pairings", status_code=202, response_model=Job, summary="Pair a device")
def start_pairing(body: PairingIn) -> Job:
    """The device must be in pairing mode. Add it to the configuration afterwards to make it a speaker."""
    return bt.start_pairing(
        body.mac,
        body.adapter,
        quiesce_adapter=body.quiesce_adapter,
        no_input_no_output_agent=body.no_input_no_output_agent,
        allow_hfp_profile=body.allow_hfp_profile,
    )


@router.post("/bluetooth/resets", status_code=202, response_model=Job, summary="Forget, power-cycle, pair again")
def start_reset(body: ResetIn) -> Job:
    return bt.start_reset(
        body.mac,
        body.adapter,
        no_input_no_output_agent=body.no_input_no_output_agent,
        allow_hfp_profile=body.allow_hfp_profile,
    )


@router.get("/bluetooth/devices", response_model=list[KnownDevice], summary="Devices BlueZ knows")
def known_devices(
    named_only: bool = Query(default=True, description="Hide entries without a name"),
) -> list[KnownDevice]:
    return [KnownDevice(**d) for d in bt.known_devices(named_only=named_only)]


@router.get("/bluetooth/devices/{mac}", response_model=dict[str, Any], summary="BlueZ's view of one device")
def device_info(mac: str, adapter: str = "") -> dict[str, Any]:
    return bt.device_info(mac, adapter)


@router.post("/bluetooth/devices/{mac}/disconnect", status_code=204, summary="Disconnect, keep the bond")
def disconnect(mac: str) -> Response:
    bt.disconnect_device(mac)
    return Response(status_code=204)


@router.delete("/bluetooth/devices/{mac}", status_code=204, summary="Forget a device (remove the bond)")
def remove(mac: str, adapter_mac: str = "") -> Response:
    bt.remove_device(mac, adapter_mac)
    return Response(status_code=204)
