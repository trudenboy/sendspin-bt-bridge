/* Pure adapters from the API/store entities to the shared card view-model. */
import { deviceState, isDeviceConnected } from "@/utils/deviceStatus";
import { speakerName } from "@/utils/speakerName";
import type {
  BtDeviceSummary,
  Device,
  SourceInputStatus,
  SourceState,
} from "@/api/types";
import type { DisabledPlayer } from "@/stores/devices";
import type { SourceToggleState, SourceTransition } from "@/utils/sourceMode";
import type {
  CardDot,
  CardTone,
  DeviceCardAction,
  DeviceCardStatus,
  DeviceCardView,
} from "./types";

const SHORT_ID = (mac: string | null | undefined): string | null => {
  if (!mac) return null;
  const tail = mac
    .replace(/[^0-9A-Fa-f]/g, "")
    .slice(-4)
    .toUpperCase();
  return tail || null;
};

const PLAYER_LOOK: Record<string, { tone: CardTone; dot: CardDot }> = {
  streaming: { tone: "success", dot: "streaming" },
  ready: { tone: "neutral", dot: "online" },
  transitioning: { tone: "warning", dot: "connecting" },
  recovering: { tone: "warning", dot: "connecting" },
  degraded: { tone: "error", dot: "error" },
  offline: { tone: "neutral", dot: "offline" },
  standby: { tone: "neutral", dot: "standby" },
  released: { tone: "neutral", dot: "standby" },
  output_missing: { tone: "error", dot: "error" },
  disabled: { tone: "neutral", dot: "offline" },
  stopping: { tone: "warning", dot: "connecting" },
  reconnecting: { tone: "warning", dot: "connecting" },
  buffering: { tone: "warning", dot: "connecting" },
  sink_muted: { tone: "warning", dot: "error" },
};

function playerStatus(device: Device): DeviceCardStatus {
  const code = deviceState(device);
  const look = PLAYER_LOOK[code] ?? {
    tone: "neutral" as const,
    dot: "offline" as const,
  };
  return {
    labelKey: code === 'ready' ? 'device.status.connected' : PLAYER_LOOK[code] ? `device.status.${code}` : code,
    tone: look.tone,
    dot: look.dot,
  };
}

function playerTransport(device: Device): string | null {
  if (device.backend?.type === "pulse_sink") return "local";
  return device.bluetooth?.adapter?.hci || "bluetooth";
}

/**
 * The chip a card wears instead of its mode while a source-mode command runs:
 * the switch is the one thing the operator is waiting for, so it takes the
 * status chip's place rather than sitting beside the stale state.
 */
const TRANSITION_STATUS: Record<SourceTransition, DeviceCardStatus> = {
  "to-source": {
    labelKey: "devices.sources.switchingToSource",
    tone: "warning",
    dot: "connecting",
  },
  "to-playback": {
    labelKey: "devices.sources.switchingToPlayback",
    tone: "warning",
    dot: "connecting",
  },
};

export function playerCardView(
  device: Device,
  transition: SourceTransition | null = null,
): DeviceCardView {
  const name = speakerName(device.name, device.bluetooth?.mac ?? device.id);
  return {
    id: device.id,
    role: "output",
    identity: {
      name,
      room: device.room?.name,
      technicalId: device.bluetooth?.mac ?? device.id,
    },
    status: transition ? TRANSITION_STATUS[transition] : playerStatus(device),
    meta: {
      room: device.room?.name,
      transport: playerTransport(device),
      shortId: SHORT_ID(device.bluetooth?.mac ?? device.id),
    },
    contextKey: device.playback.playing
      ? "device.context.playing"
      : "device.context.nothingPlaying",
    overflowGroups: [],
  };
}

export function disabledCardView(player: DisabledPlayer): DeviceCardView {
  const name = speakerName(player.name, player.mac ?? player.id);
  return {
    id: player.id,
    role: "disabled",
    identity: { name, room: player.room, technicalId: player.mac ?? player.id },
    status: {
      labelKey: "device.status.disabled",
      tone: "neutral",
      dot: "offline",
    },
    meta: {
      room: player.room,
      shortId: SHORT_ID(player.mac ?? player.id),
      adapter: player.adapter,
    },
    contextKey: "device.context.disabled",
    primary: { id: "enable", labelKey: "speaker.enable" },
    overflowGroups: [],
  };
}

export function hardwareCardView(
  device: BtDeviceSummary,
  sourceDevice = false,
): DeviceCardView {
  const name = device.alias || device.name || device.mac;
  const connected = device.connected === true;
  const bonded = device.paired === true || device.bonded === true;
  const status: DeviceCardStatus = connected
    ? { labelKey: "hardware.connected", tone: "success", dot: "online" }
    : bonded
      ? { labelKey: "hardware.paired", tone: "neutral", dot: "offline" }
      : { labelKey: "hardware.notConnected", tone: "warning", dot: "offline" };
  return {
    id: device.mac,
    role: sourceDevice
      ? "input"
      : device.roles.some((role) => role.role === "input")
        ? "input"
        : "output",
    identity: { name, technicalId: device.mac },
    status,
    meta: { shortId: SHORT_ID(device.mac) },
    contextKey: sourceDevice
      ? "hardware.context.sourceOnly"
      : "hardware.context.bond",
    primary: connected
      ? { id: "open", labelKey: "hardware.openDetailsShort" }
      : bonded
        ? { id: "connect", labelKey: "hardware.connect" }
        : undefined,
    overflowGroups: [],
  };
}

/**
 * The same player, in the Sources section: same identity and shell, but the
 * link is now an input. Streaming means the capture is open; anything else is
 * the operator's "waiting for stream" story (a disconnect never changes mode).
 */
export function sourceModeCardView(
  device: Device,
  state: SourceState | undefined,
  transition: SourceTransition | null = null,
  captured = true,
): DeviceCardView {
  const name = speakerName(device.name, device.bluetooth?.mac ?? device.id);
  // A disconnect does not change the mode: the card waits for the stream to
  // come back rather than reporting the capture's transient error. The feed can
  // also be streaming to Music Assistant while another input is the one open,
  // so "streaming" only describes this card when its own capture is open.
  const streaming = state === "streaming" && captured;
  const failed = state === "error" && isDeviceConnected(device);
  const status: DeviceCardStatus =
    transition !== null
      ? TRANSITION_STATUS[transition]
      : failed
        ? { labelKey: "devices.sources.error", tone: "error", dot: "error" }
        : streaming
          ? {
              labelKey: "devices.sources.streaming",
              tone: "success",
              dot: "streaming",
            }
          : {
              labelKey: "devices.sources.waitingForStream",
              tone: "neutral",
              dot: "standby",
            };
  return {
    id: device.id,
    role: "input",
    identity: {
      name,
      room: device.room?.name,
      technicalId: device.bluetooth?.mac ?? device.id,
    },
    status,
    meta: {
      room: device.room?.name,
      transport: playerTransport(device),
      shortId: SHORT_ID(device.bluetooth?.mac ?? device.id),
    },
    contextKey: "devices.sources.capturing",
    overflowGroups: [],
  };
}

/** The source-mode toggle as the shell's one primary action, or nothing when hidden. */
export function sourceToggleAction(
  id: string,
  toggle: SourceToggleState,
): DeviceCardAction | undefined {
  if (!toggle.visible) return undefined;
  return {
    id,
    labelKey: toggle.labelKey,
    disabled: toggle.disabled,
    hintKey: toggle.disabled ? (toggle.reasonKey ?? undefined) : undefined,
  };
}

export function sourceCardView(
  input: SourceInputStatus,
  state: SourceState | undefined,
): DeviceCardView {
  const active = input.active === true;
  const status: DeviceCardStatus = !input.available
    ? { labelKey: "devices.sources.unavailable", tone: "warning", dot: "error" }
    : state === "error"
      ? { labelKey: "devices.sources.error", tone: "error", dot: "error" }
      : active && state === "streaming"
        ? {
            labelKey: "devices.sources.streaming",
            tone: "success",
            dot: "streaming",
          }
        : active
          ? { labelKey: "devices.sources.active", tone: "info", dot: "online" }
          : {
              labelKey: "devices.sources.available",
              tone: "neutral",
              dot: "ready",
            };
  return {
    id: input.id,
    role: "input",
    identity: {
      name: input.name || input.description || input.id,
      technicalId: input.mac ?? input.node ?? input.id,
    },
    status,
    meta: {
      shortId: SHORT_ID(input.mac),
      transport:
        input.kind === "headset_microphone" ? "microphone" : input.kind,
    },
    contextKey: active ? "devices.sources.active" : "devices.sources.available",
    primary:
      active || !input.available
        ? undefined
        : { id: "use", labelKey: "devices.sources.use" },
    overflowGroups: [],
  };
}
