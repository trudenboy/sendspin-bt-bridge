# Domain language

Names this codebase uses for the things it manages. One entry per term, in
the words the code and the operator share. Architecture vocabulary (module,
interface, seam, adapter, depth) lives in the `codebase-design` skill, not
here.

## Bluetooth

**Device address** — a speaker's Bluetooth address, canonical and comparable,
written three ways depending on who is asking: colons for BlueZ and the config
(`AA:BB:CC:DD:EE:FF`), underscores for audio sink names and D-Bus paths, bare
for the sysfs adapter map. `DeviceAddress` parses any of them and renders
whichever a consumer needs. Never compare address strings directly.

**Controller** — one physical Bluetooth adapter, identified by its own device
address and by the kernel's `hciN` name. The two are mapped through the
adapter map, which is keyed by the bare address form. An operator sees a
controller by its friendly name; the code decides by address.

**Adapter handle** — the object through which a speaker reaches its
controller. It resolves `hciN`, owns the process-wide operation lease, and is
the single place that decides which controller an operation runs against.

**Lease** — permission to run one blocking Bluetooth operation at a time
(pair, scan, reconnect, RSSI refresh). Taken from the adapter handle and
released by token, so a stale release cannot free somebody else's operation.

**Bluetooth device module** — one speaker's life on the BlueZ D-Bus: it
resolves the speaker's object through `ObjectManager` on a named controller,
owns the bus connection and the `PropertiesChanged` subscription, answers
named questions about the speaker (`is_connected`, `is_paired`, `uuids`,
`battery_level`, `transport_state`, `services_resolved`), returns a whole
consistent `state()` when a caller needs several at once, connects a profile
and drops the link. It does not pair, connect, trust or remove — those are
the controller verbs.

**Controller verbs** — power a controller up or down, and connect, trust or
forget a speaker through it. Each answers with a verdict — it worked, BlueZ
refused and here is why, nothing answered — never with the transcript of the
command that ran. Two transports satisfy them: BlueZ's own bus, which names
the controller by object path so an operation cannot land on the wrong one,
and `bluetoothctl`, which runs when the bus could not answer at all. A
refusal is an answer, and is never retried through the other transport.

**Pairing agent** — the `org.bluez.Agent1` this bridge registers for the
duration of one pair attempt, bound to the target address. Auto-confirms
secure simple pairing and authorises A2DP and AVRCP; HFP and
`NoInputNoOutput` are explicit one-shot choices, never defaults.

## Audio

**Sink name** — what the audio server calls a speaker's output. PipeWire,
WirePlumber and PulseAudio each spell it differently, so the bridge tries
several candidates when connecting and must be able to read any of them back.
Written and parsed by one grammar.

**Daemon** — the per-speaker subprocess that runs the Sendspin client.
One speaker, one daemon, one Player.

**Player** — the daemon's audio output. It takes PCM chunks with a play
time and renders them through GStreamer to a named sink.

**Identity** — the long-term X25519 keypair a daemon presents on the
Sendspin wire. Persisted per speaker so Music Assistant sees the same
client after a restart.

**Pairing store** — the credentials a daemon keeps for servers it has
paired with, plus the last playback server. Persisted next to the identity.

## Music Assistant

**Player id** — Music Assistant's own id for the player fronting one of our
speakers. It is published, not derivable: MA lists our client id among that
player's output protocols, and the bridge learns the mapping when it connects.
Queue commands carry the learned id.

**Sync group** — a Music Assistant group of players sharing one queue. A
speaker in no sync group still has a queue of its own, addressed by its
player id.
