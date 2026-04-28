"""Home Assistant MQTT-discovery publisher.

Owns one asyncio task that:

* connects to an MQTT broker (auto-detected via Supervisor on HAOS, or
  configured manually),
* publishes Home Assistant *discovery* payloads under
  ``<discovery_prefix>/<component>/sendspin_<player_id>/<object_id>/config``,
* mirrors the bridge's read-side state to ``sendspin/<player_id>/state``
  topics whenever ``services/internal_events.py`` publishes a delta,
* subscribes to ``sendspin/<player_id>/cmd/<command>`` and dispatches via
  ``services/ha_command_dispatcher.py``.

The publisher must NEVER expose entities that Music Assistant's HA
integration already owns (``media_player`` and its volume / mute /
transport / queue / metadata fields) — that contract is enforced upstream
by ``services/ha_entity_model.py``.  Each discovery payload sets
``device.connections=[("bluetooth", mac)]`` so HA's device registry
merges our diagnostics into the same device card MA created.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from services.ha_entity_model import (
    BRIDGE_ENTITIES,
    DEVICE_ENTITIES,
    EntityKind,
    EntitySpec,
    bridge_unique_id,
    device_unique_id,
)
from services.ha_state_projector import (
    HAStateProjection,
    StateDelta,
    compute_delta,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MqttPublisherConfig:
    """Resolved MQTT settings the publisher actually connects with.

    Built from ``config["HA_INTEGRATION"]`` after broker auto-detect.  Auto
    mode (``broker == "auto"``) is resolved by ``ha_addon.get_mqtt_addon_credentials``
    before constructing this dataclass.
    """

    enabled: bool
    host: str
    port: int
    username: str
    password: str
    discovery_prefix: str
    tls: bool
    client_id: str
    bridge_id: str
    bridge_name: str

    @property
    def state_topic_root(self) -> str:
        return "sendspin"

    def availability_topic_bridge(self) -> str:
        return f"{self.state_topic_root}/bridge/availability"

    def availability_topic_device(self, player_id: str) -> str:
        return f"{self.state_topic_root}/{player_id}/availability"

    def state_topic_device(self, player_id: str) -> str:
        return f"{self.state_topic_root}/{player_id}/state"

    def state_topic_bridge(self) -> str:
        return f"{self.state_topic_root}/bridge/state"

    def cmd_topic_device(self, player_id: str, command: str) -> str:
        return f"{self.state_topic_root}/{player_id}/cmd/{command}"

    def cmd_topic_bridge(self, command: str) -> str:
        return f"{self.state_topic_root}/bridge/cmd/{command}"

    def discovery_topic(self, component: str, unique_id: str) -> str:
        return f"{self.discovery_prefix}/{component}/{unique_id}/config"


def resolve_mqtt_config(
    ha_integration: dict[str, Any] | None,
    *,
    bridge_id: str,
    bridge_name: str,
    auto_lookup: Callable[[], dict[str, Any] | None] | None = None,
) -> MqttPublisherConfig | None:
    """Translate a config block into a connect-ready ``MqttPublisherConfig``.

    Returns ``None`` when the integration is disabled, when ``mode`` is not
    ``mqtt``/``both``, or when ``broker == "auto"`` and Supervisor returns
    no MQTT add-on.  Callers should distinguish "disabled" from
    "broker missing" via ``log_resolution_diagnostic`` (added in path B
    follow-up); for now we just log and return None.
    """
    block = ha_integration or {}
    if not block.get("enabled"):
        return None
    mode = str(block.get("mode") or "off").lower()
    # ``both`` was removed in v2.65.0-rc.3 — treat any pre-existing value
    # from saved configs as ``mqtt`` so an upgrade doesn't silently stop
    # publishing.  ``config_migration._normalize_ha_integration`` rewrites
    # the stored value on the next save.
    if mode == "both":
        mode = "mqtt"
    if mode != "mqtt":
        return None
    mqtt_block = dict(block.get("mqtt") or {})

    broker = str(mqtt_block.get("broker") or "auto").strip()
    port = int(mqtt_block.get("port") or 1883)
    username = str(mqtt_block.get("username") or "")
    password = str(mqtt_block.get("password") or "")
    tls = bool(mqtt_block.get("tls"))
    client_id = str(mqtt_block.get("client_id") or "") or f"sendspin_{bridge_id}"
    discovery_prefix = str(mqtt_block.get("discovery_prefix") or "homeassistant").strip() or "homeassistant"

    if broker.lower() == "auto":
        creds = auto_lookup() if auto_lookup else None
        if not creds:
            logger.info("HA MQTT: broker=auto but Supervisor MQTT service unavailable; publisher disabled")
            return None
        host = creds["host"]
        port = int(creds.get("port") or port or 1883)
        username = username or creds.get("username", "")
        password = password or creds.get("password", "")
        tls = tls or bool(creds.get("ssl"))
    else:
        # ``broker`` may be a bare hostname or ``host:port``.
        if ":" in broker and not broker.startswith("["):  # IPv4 / hostname only — IPv6 needs brackets
            host_part, _, port_part = broker.rpartition(":")
            try:
                port = int(port_part)
            except ValueError:
                host_part = broker
            host = host_part or broker
        else:
            host = broker

    return MqttPublisherConfig(
        enabled=True,
        host=host,
        port=port,
        username=username,
        password=password,
        discovery_prefix=discovery_prefix,
        tls=tls,
        client_id=client_id,
        bridge_id=bridge_id,
        bridge_name=bridge_name,
    )


# ---------------------------------------------------------------------------
# Discovery payload builder
# ---------------------------------------------------------------------------


def _device_block(meta, bridge_meta) -> dict[str, Any]:
    """Build the ``device`` discovery block for a per-speaker entity.

    Critical: ``connections=[("bluetooth", mac)]`` is what makes HA merge
    our entities with MA's existing ``media_player.<name>`` device card.
    """
    block: dict[str, Any] = {
        "identifiers": [f"sendspin_{meta.player_id}"],
        "name": meta.player_name or meta.player_id,
        "manufacturer": "Sendspin",
        "model": "BT Speaker via Sendspin Bridge",
    }
    if meta.mac:
        block["connections"] = [["bluetooth", meta.mac]]
    if bridge_meta:
        block["via_device"] = f"sendspin_bridge_{bridge_meta.bridge_id}"
    if meta.adapter_name:
        block.setdefault("hw_version", meta.adapter_name)
    if meta.room_name:
        block["suggested_area"] = meta.room_name
    return block


def _bridge_device_block(bridge_meta) -> dict[str, Any]:
    if bridge_meta is None:
        return {}
    block: dict[str, Any] = {
        "identifiers": [f"sendspin_bridge_{bridge_meta.bridge_id}"],
        "name": f"Sendspin Bridge: {bridge_meta.bridge_name}".strip(": "),
        "manufacturer": "Sendspin",
        "model": "Music Assistant ↔ Bluetooth Bridge",
        "sw_version": bridge_meta.version or "",
    }
    if bridge_meta.web_url:
        block["configuration_url"] = bridge_meta.web_url
    return block


def _component_for_kind(kind: EntityKind) -> str:
    return kind.value


def _payload_for_device_spec(
    spec: EntitySpec,
    *,
    cfg: MqttPublisherConfig,
    meta,
    bridge_meta,
) -> tuple[str, dict[str, Any]]:
    """Build (discovery_topic, payload) for one per-device entity spec."""
    uid = device_unique_id(meta.player_id, spec)
    payload: dict[str, Any] = {
        "name": spec.name,
        "unique_id": uid,
        "object_id": uid,
        "device": _device_block(meta, bridge_meta),
    }
    if spec.entity_category:
        payload["entity_category"] = spec.entity_category
    if spec.icon:
        payload["icon"] = spec.icon

    state_topic = cfg.state_topic_device(meta.player_id)
    availability_topic = cfg.availability_topic_device(meta.player_id)

    if spec.kind is EntityKind.BUTTON:
        # Buttons publish to a command topic only; no state.
        payload.update(
            {
                "command_topic": cfg.cmd_topic_device(meta.player_id, spec.command or spec.object_id),
                "payload_press": "PRESS",
                "availability_topic": availability_topic,
            }
        )
    else:
        # Stateful entities consume a single per-device JSON state topic.
        payload.update(
            {
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{spec.object_id} }}}}",
                "availability_topic": availability_topic,
            }
        )
        if spec.device_class:
            payload["device_class"] = spec.device_class
        if spec.state_class:
            payload["state_class"] = spec.state_class
        if spec.unit:
            payload["unit_of_measurement"] = spec.unit

        if spec.kind is EntityKind.SWITCH:
            payload.update(
                {
                    "command_topic": cfg.cmd_topic_device(meta.player_id, spec.command or spec.object_id),
                    "payload_on": spec.payload_on,
                    "payload_off": spec.payload_off,
                }
            )
        elif spec.kind is EntityKind.SELECT:
            payload.update(
                {
                    "command_topic": cfg.cmd_topic_device(meta.player_id, spec.command or spec.object_id),
                    "options": list(spec.options),
                }
            )
        elif spec.kind is EntityKind.NUMBER:
            payload.update(
                {
                    "command_topic": cfg.cmd_topic_device(meta.player_id, spec.command or spec.object_id),
                    "min": spec.min_value,
                    "max": spec.max_value,
                    "step": spec.step,
                }
            )

    component = _component_for_kind(spec.kind)
    return cfg.discovery_topic(component, uid), payload


def _payload_for_bridge_spec(
    spec: EntitySpec,
    *,
    cfg: MqttPublisherConfig,
    bridge_meta,
) -> tuple[str, dict[str, Any]]:
    uid = bridge_unique_id(bridge_meta.bridge_id, spec)
    payload: dict[str, Any] = {
        "name": spec.name,
        "unique_id": uid,
        "object_id": uid,
        "device": _bridge_device_block(bridge_meta),
    }
    if spec.entity_category:
        payload["entity_category"] = spec.entity_category
    if spec.icon:
        payload["icon"] = spec.icon

    availability_topic = cfg.availability_topic_bridge()
    state_topic = cfg.state_topic_bridge()

    if spec.kind is EntityKind.BUTTON:
        payload.update(
            {
                "command_topic": cfg.cmd_topic_bridge(spec.command or spec.object_id),
                "payload_press": "PRESS",
                "availability_topic": availability_topic,
            }
        )
    elif spec.kind is EntityKind.UPDATE:
        # HA Update entity: needs latest_version_topic + installed_version_topic.
        payload.update(
            {
                "state_topic": state_topic,
                "value_template": "{{ value_json.update_available_state | default('off') }}",
                "latest_version_topic": state_topic,
                "latest_version_template": "{{ value_json.update_available_attrs.latest_version | default('') }}",
                "availability_topic": availability_topic,
            }
        )
    else:
        payload.update(
            {
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{spec.object_id} }}}}",
                "availability_topic": availability_topic,
            }
        )
        if spec.device_class:
            payload["device_class"] = spec.device_class

    return cfg.discovery_topic(_component_for_kind(spec.kind), uid), payload


def build_discovery_payloads(
    cfg: MqttPublisherConfig, projection: HAStateProjection
) -> list[tuple[str, dict[str, Any]]]:
    """Compute every retained ``…/config`` payload for the given projection."""
    out: list[tuple[str, dict[str, Any]]] = []
    bridge_meta = projection.bridge_meta
    for _player_id, meta in projection.device_meta.items():
        for spec in DEVICE_ENTITIES:
            topic, payload = _payload_for_device_spec(spec, cfg=cfg, meta=meta, bridge_meta=bridge_meta)
            out.append((topic, payload))
    if bridge_meta is not None:
        for spec in BRIDGE_ENTITIES:
            topic, payload = _payload_for_bridge_spec(spec, cfg=cfg, bridge_meta=bridge_meta)
            out.append((topic, payload))
    return out


# ---------------------------------------------------------------------------
# State payload builder
# ---------------------------------------------------------------------------


def _state_value_for_publish(spec: EntitySpec, value: Any) -> Any:
    """Normalise an entity value to its HA wire form."""
    if spec.kind in (EntityKind.BINARY_SENSOR, EntityKind.SWITCH):
        if value is None:
            return spec.payload_off
        return spec.payload_on if bool(value) else spec.payload_off
    return value


def build_device_state_payload(projection: HAStateProjection, player_id: str) -> dict[str, Any]:
    """Consolidated JSON state for a per-device state topic."""
    entities = projection.devices.get(player_id, {})
    out: dict[str, Any] = {}
    spec_index = {s.object_id: s for s in DEVICE_ENTITIES}
    for object_id, state in entities.items():
        spec = spec_index.get(object_id)
        if spec is None:
            continue
        out[object_id] = _state_value_for_publish(spec, state.value)
    return out


def build_bridge_state_payload(projection: HAStateProjection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    spec_index = {s.object_id: s for s in BRIDGE_ENTITIES}
    for object_id, state in projection.bridge.items():
        spec = spec_index.get(object_id)
        if spec is None:
            continue
        if spec.kind is EntityKind.UPDATE:
            # HA's update entity wants ON/OFF for "update available" plus
            # nested attrs for the latest_version template.
            out["update_available_state"] = "ON" if bool(state.value) else "OFF"
            out["update_available_attrs"] = dict(state.attrs)
            continue
        out[object_id] = _state_value_for_publish(spec, state.value)
    return out


# ---------------------------------------------------------------------------
# Publisher (asyncio)
# ---------------------------------------------------------------------------


class HaMqttPublisher:
    """Async publisher task; one instance per running bridge process.

    Construct with closures so the publisher can re-resolve config at
    every reconnect cycle without holding stale references.
    """

    def __init__(
        self,
        *,
        config_provider: Callable[[], MqttPublisherConfig | None],
        projection_provider: Callable[[], HAStateProjection],
        dispatcher,  # services.ha_command_dispatcher.HaCommandDispatcher
        event_subscribe: Callable[[Callable[[Any], None]], Callable[[], None]],
        heartbeat_seconds: float = 30.0,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self._config_provider = config_provider
        self._projection_provider = projection_provider
        self._dispatcher = dispatcher
        self._event_subscribe = event_subscribe
        self._heartbeat_seconds = heartbeat_seconds
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max

        self._stop_event = asyncio.Event()
        self._dirty_event = asyncio.Event()
        self._unsubscribe: Callable[[], None] | None = None
        self._task: asyncio.Task | None = None
        self._client: Any = None  # aiomqtt.Client when connected
        self._last_projection: HAStateProjection | None = None

        # Diagnostics (read by the web UI status panel).
        self.state: str = "idle"
        self.last_error: str | None = None
        self.connected_broker: str | None = None
        self.discovery_payload_count: int = 0
        self.last_event_at: str | None = None
        self.published_messages: int = 0

    # -- lifecycle ----------------------------------------------------

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        # Subscribe synchronously so we don't lose events between start()
        # and the first time the run loop reaches the await.
        self._unsubscribe = self._event_subscribe(self._on_internal_event)
        self._task = asyncio.create_task(self._run(), name="ha_mqtt_publisher")
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        self._dirty_event.set()
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:  # pragma: no cover
                pass
            self._unsubscribe = None
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._client = None
        self.state = "stopped"

    # -- internal events bridge --------------------------------------

    def _on_internal_event(self, event: Any) -> None:
        """Sync callback from internal_events.publish — flag the loop dirty.

        Intentionally tiny: the publish loop owns asyncio context, this
        callback fires from arbitrary threads.  We flip an asyncio.Event
        through the running loop in a thread-safe way.
        """
        loop = self._loop_or_none()
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._dirty_event.set)
            self.last_event_at = getattr(event, "at", None)
        except RuntimeError:
            pass

    def _loop_or_none(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            try:
                # Fall back to the bridge's main loop.
                import state as live_state

                return live_state.get_main_loop()
            except Exception:
                return None

    # -- main loop ---------------------------------------------------

    async def _run(self) -> None:
        backoff = self._backoff_initial
        while not self._stop_event.is_set():
            cfg = self._config_provider()
            if cfg is None:
                self.state = "disabled"
                # Wait for either stop or a config change; the
                # orchestrator pokes us via stop()+start() on enable.
                await asyncio.sleep(2.0)
                continue
            try:
                self.state = "connecting"
                await self._serve(cfg)
                backoff = self._backoff_initial
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = "error"
                self.last_error = str(exc)
                logger.warning("HA MQTT publisher error: %s — reconnect in %.1fs", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    if self._stop_event.is_set():
                        break
                except TimeoutError:
                    pass
                backoff = min(backoff * 2.0, self._backoff_max)
        self.state = "stopped"

    async def _serve(self, cfg: MqttPublisherConfig) -> None:
        try:
            import aiomqtt
        except ImportError as exc:
            raise RuntimeError("aiomqtt is required for the HA MQTT publisher") from exc

        will = aiomqtt.Will(
            topic=cfg.availability_topic_bridge(),
            payload="offline",
            qos=1,
            retain=True,
        )

        async with aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
            identifier=cfg.client_id,
            tls_params=aiomqtt.TLSParameters() if cfg.tls else None,
            will=will,
        ) as client:
            self._client = client
            self.connected_broker = f"{cfg.host}:{cfg.port}"
            self.state = "connected"
            self.last_error = None

            await self._on_connect(client, cfg)

            tasks = [
                asyncio.create_task(self._command_loop(client, cfg), name="ha_mqtt_cmd"),
                asyncio.create_task(self._publish_loop(client, cfg), name="ha_mqtt_pub"),
            ]
            try:
                await self._stop_event.wait()
            finally:
                # Flush 'offline' before closing if we can.
                try:
                    await client.publish(cfg.availability_topic_bridge(), "offline", qos=1, retain=True)
                except Exception:  # pragma: no cover
                    pass
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                self._client = None
                self.state = "disconnected"

    # -- on-connect publish ------------------------------------------

    async def _on_connect(self, client, cfg: MqttPublisherConfig) -> None:
        # Subscribe to command topics first so we don't miss commands
        # arriving in response to discovery.
        await client.subscribe(f"{cfg.state_topic_root}/+/cmd/+", qos=1)
        await client.subscribe(f"{cfg.state_topic_root}/bridge/cmd/+", qos=1)

        # Publish bridge availability.
        await client.publish(cfg.availability_topic_bridge(), "online", qos=1, retain=True)

        projection = self._projection_provider()
        await self._publish_full_state(client, cfg, projection)
        self._last_projection = projection
        self._dirty_event.clear()

    async def _publish_full_state(self, client, cfg: MqttPublisherConfig, projection: HAStateProjection) -> None:
        # Discovery payloads (retained — survives broker restarts).
        payloads = build_discovery_payloads(cfg, projection)
        for topic, payload in payloads:
            await client.publish(topic, json.dumps(payload), qos=1, retain=True)
            self.published_messages += 1
        self.discovery_payload_count = len(payloads)

        # Per-device availability + state.
        for player_id, online in projection.availability.items():
            await client.publish(
                cfg.availability_topic_device(player_id),
                "online" if online else "offline",
                qos=1,
                retain=True,
            )
            await client.publish(
                cfg.state_topic_device(player_id),
                json.dumps(build_device_state_payload(projection, player_id)),
                qos=1,
                retain=True,
            )
            self.published_messages += 2

        # Bridge state.
        await client.publish(
            cfg.state_topic_bridge(),
            json.dumps(build_bridge_state_payload(projection)),
            qos=1,
            retain=True,
        )
        self.published_messages += 1

    # -- delta publish loop ------------------------------------------

    async def _publish_loop(self, client, cfg: MqttPublisherConfig) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._dirty_event.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                # Heartbeat republish — corrects any silent drift since the
                # last delta publication.
                projection = self._projection_provider()
                await self._publish_full_state(client, cfg, projection)
                self._last_projection = projection
                continue
            self._dirty_event.clear()
            if self._stop_event.is_set():
                break
            projection = self._projection_provider()
            delta = compute_delta(self._last_projection, projection)
            await self._publish_delta(client, cfg, projection, delta)
            self._last_projection = projection

    async def _publish_delta(
        self,
        client,
        cfg: MqttPublisherConfig,
        projection: HAStateProjection,
        delta: StateDelta,
    ) -> None:
        # Newly added devices need fresh discovery payloads + initial state.
        if delta.devices_added:
            for topic, payload in build_discovery_payloads(cfg, projection):
                await client.publish(topic, json.dumps(payload), qos=1, retain=True)
                self.published_messages += 1
            self.discovery_payload_count = sum(1 for _ in build_discovery_payloads(cfg, projection))

        for player_id, _entities in delta.devices.items():
            await client.publish(
                cfg.state_topic_device(player_id),
                json.dumps(build_device_state_payload(projection, player_id)),
                qos=1,
                retain=True,
            )
            self.published_messages += 1

        if delta.bridge:
            await client.publish(
                cfg.state_topic_bridge(),
                json.dumps(build_bridge_state_payload(projection)),
                qos=1,
                retain=True,
            )
            self.published_messages += 1

        for player_id, online in delta.availability_changed.items():
            await client.publish(
                cfg.availability_topic_device(player_id),
                "online" if online else "offline",
                qos=1,
                retain=True,
            )
            self.published_messages += 1

        if delta.devices_removed:
            # Clear retained config + state for the removed devices so HA
            # cleans up its registry.
            for player_id in delta.devices_removed:
                for spec in DEVICE_ENTITIES:
                    uid = device_unique_id(player_id, spec)
                    topic = cfg.discovery_topic(_component_for_kind(spec.kind), uid)
                    await client.publish(topic, "", qos=1, retain=True)
                await client.publish(cfg.availability_topic_device(player_id), "offline", qos=1, retain=True)
            self.published_messages += len(delta.devices_removed) * (len(DEVICE_ENTITIES) + 1)

    # -- command subscription loop -----------------------------------

    async def _command_loop(self, client, cfg: MqttPublisherConfig) -> None:
        try:
            async for msg in client.messages:
                if self._stop_event.is_set():
                    break
                try:
                    self._handle_command(msg, cfg)
                except Exception:  # pragma: no cover
                    logger.exception("HA MQTT command handler raised")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("HA MQTT command loop error: %s", exc)
            raise

    def _handle_command(self, msg, cfg: MqttPublisherConfig) -> None:
        topic = str(msg.topic)
        payload = (
            msg.payload.decode("utf-8", errors="replace")
            if isinstance(msg.payload, (bytes, bytearray))
            else str(msg.payload)
        )
        # Topic shape: <root>/<player_id|bridge>/cmd/<command>
        parts = topic.split("/")
        if len(parts) != 4 or parts[2] != "cmd":
            return
        scope = parts[1]
        command = parts[3]

        # JSON payloads are accepted for typed commands (numbers, selects);
        # bare strings ("ON" / "OFF" / "PRESS" / option names) work too.
        try:
            value: Any = json.loads(payload)
        except (ValueError, TypeError):
            value = payload

        if scope == "bridge":
            self._dispatcher.dispatch_bridge(command, value)
        else:
            self._dispatcher.dispatch_device(scope, command, value)


# ---------------------------------------------------------------------------
# Diagnostics surface (consumed by /api/ha/mqtt/status web UI route)
# ---------------------------------------------------------------------------


def publisher_status(publisher: HaMqttPublisher | None) -> dict[str, Any]:
    if publisher is None:
        return {
            "running": False,
            "state": "idle",
            "broker": None,
            "discovery_payload_count": 0,
            "published_messages": 0,
            "last_error": None,
            "last_event_at": None,
        }
    return {
        "running": publisher.state == "connected",
        "state": publisher.state,
        "broker": publisher.connected_broker,
        "discovery_payload_count": publisher.discovery_payload_count,
        "published_messages": publisher.published_messages,
        "last_error": publisher.last_error,
        "last_event_at": publisher.last_event_at,
    }


__all__ = [
    "HaMqttPublisher",
    "MqttPublisherConfig",
    "build_bridge_state_payload",
    "build_device_state_payload",
    "build_discovery_payloads",
    "publisher_status",
    "resolve_mqtt_config",
]
