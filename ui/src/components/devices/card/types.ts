import type { Component } from "vue";

export type DeviceCardRole = "output" | "input" | "bluetooth" | "disabled";
export type CardTone = "success" | "warning" | "error" | "info" | "neutral";
export type CardDot =
  | "online"
  | "streaming"
  | "ready"
  | "connecting"
  | "error"
  | "offline"
  | "standby";
export type CardDensity = "comfortable" | "compact";

export interface DeviceCardStatus {
  /** Translation key; the card translates it so view-models stay pure. */
  labelKey: string;
  params?: Record<string, unknown>;
  tone: CardTone;
  dot: CardDot;
  /** Optional technical sentence, shown as the badge title. */
  title?: string;
}

export interface DeviceCardIdentity {
  name: string;
  room?: string | null;
  /** Full technical id, only for title/drawer. */
  technicalId?: string | null;
}

export interface DeviceCardMeta {
  room?: string | null;
  transport?: string | null;
  shortId?: string | null;
  adapter?: string | null;
}

export interface DeviceCardAction {
  id: string;
  labelKey: string;
  icon?: Component;
  disabled?: boolean;
  /** Why it is disabled: a translation key, shown under the button. */
  hintKey?: string;
  title?: string;
  destructive?: boolean;
}

export interface DeviceCardActionGroup {
  id: string;
  labelKey?: string;
  actions: DeviceCardAction[];
}

export interface DeviceCardView {
  id: string;
  role: DeviceCardRole;
  identity: DeviceCardIdentity;
  status: DeviceCardStatus;
  meta: DeviceCardMeta;
  /** One reserved line; the card supplies the translated text. */
  contextKey?: string;
  primary?: DeviceCardAction;
  overflowGroups: DeviceCardActionGroup[];
}
