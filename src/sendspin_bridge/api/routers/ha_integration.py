"""``/ha-integration`` — the Home Assistant settings screen's lookups and probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import config as cfg
from sendspin_bridge.application import ha_integration as ha

router = APIRouter(
    prefix="/ha-integration",
    tags=["ha-integration"],
    dependencies=[Depends(require_principal)],
    responses=PROBLEM_RESPONSES,
)


class AreasIn(BaseModel):
    ha_token: str = Field(description="A transient HA access token; not stored.")
    adapters: list[dict[str, Any]] = Field(default_factory=list)
    include_devices: bool = False


class MqttTestIn(BaseModel):
    host: str
    port: int = Field(default=1883, ge=1, le=65535)
    username: str = ""
    password: str = Field(
        default="", description="'***REDACTED***' reuses the saved password for the saved broker only."
    )
    tls: bool = False


@router.post("/areas", response_model=dict[str, Any], summary="Home Assistant areas and suggestions")
def areas(body: AreasIn) -> dict[str, Any]:
    return cfg.ha_areas(body.ha_token, body.adapters, include_devices=body.include_devices)


@router.get("/mqtt", response_model=dict[str, Any], summary="MQTT publisher state")
def mqtt_status() -> dict[str, Any]:
    return ha.mqtt_status()


@router.post("/mqtt/probe", response_model=dict[str, Any], summary="Auto-detect a broker")
def mqtt_probe() -> dict[str, Any]:
    return ha.mqtt_probe()


@router.post("/mqtt/test", response_model=dict[str, Any], summary="Test broker credentials")
async def mqtt_test(body: MqttTestIn) -> dict[str, Any]:
    return await ha.mqtt_test(
        host=body.host, port=body.port, username=body.username, password=body.password, tls=body.tls
    )


@router.get("/mosquitto", response_model=dict[str, Any], summary="Mosquitto add-on state")
def mosquitto() -> dict[str, Any]:
    return ha.mosquitto_status()


@router.post("/rest/probe", response_model=dict[str, Any], summary="Address the bridge advertises")
def rest_probe() -> dict[str, Any]:
    return ha.rest_probe()


@router.get("/mdns", response_model=dict[str, Any], summary="mDNS advertisement")
def mdns() -> dict[str, Any]:
    return ha.mdns_status()


@router.get("/custom-component", response_model=dict[str, Any], summary="HACS integration state")
def custom_component() -> dict[str, Any]:
    return ha.custom_component_status()
