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

**Card** — the audio server's object for one sound device: the profiles it can
take and the ports it exposes. Every sink and source belongs to a card, and the
card is where "this output is silent" is answered — the profile is `off`, or the
active port is not available. A Bluetooth speaker has a card too; it is not the
speaker's D-Bus object and must not be confused with it.

**Audio endpoint** — a sink (output) or a source (input) as the audio server
names it. It carries a direction, a state, volume and mute for outputs, and the
identifiers (serial, bus path, vendor and product ids, ALSA long card name) that
let it be recognised after a rename. The name is how it is addressed; the
identifiers are how it is matched.

**Link role** — what a Bluetooth link is doing: `output` when the bridge plays
to the device, `input` when the device sends audio to the bridge. One device can
hold both at once. The role is derived from evidence — an active transport, a
capture node, a source — never from the spelling of a sink name.

**Source feed** — one selected audio input, its capture, and the Sendspin
source-client link to Music Assistant. The input can be a phone sending
Bluetooth audio, a saved local Audio endpoint, or a headset microphone. The
bridge captures and forwards; Music Assistant owns playback and routing. It
is not a Player and not a Device; it has no playback of its own.

**Microphone hand-off** — temporarily using a headset's HFP microphone instead
of its A2DP output. Its Player rests while the microphone is in use and can
resume only when the A2DP output is confirmed ready. A failed return leaves
the Player stopped until the operator requests recovery.

**Pairing window** — the limited time in which a phone can pair with the
bridge, or Music Assistant can pair with the Source feed using its code.
These are separate pairings. Each window belongs to the attempt and remains
open across page changes until pairing succeeds, the operator cancels, or
the window expires.

**Music Assistant pairing code** — the eight digits shown in Settings and typed
into Music Assistant to pair the bridge's source client. Separate from the
phone's Bluetooth pairing: the phone pairs with the bridge, the bridge pairs
with Music Assistant.

**Daemon** — the per-speaker subprocess that runs the Sendspin client.
One speaker, one daemon, one Player.

**Player** — the daemon's audio output. It takes PCM chunks with a play
time and renders them through GStreamer to a named sink. The GStreamer
`pulsesink` receives that sink through its explicit `device` property; routing
does not depend on the process-wide `PULSE_SINK` environment variable.

**Decoder** — bridge-owned conversion from Sendspin audio chunks to PCM. For
FLAC, the daemon creates and drives the codec decoder before handing PCM to the
Player; aiosendspin does not decode chunks on the bridge's behalf.

**Identity** — the long-term X25519 keypair a daemon presents on the
aiosendspin 9 Noise wire. Persisted per speaker so Music Assistant sees the
same client after a restart.

**Pairing store** — the credentials a daemon keeps for servers it has
paired with, plus the last playback server. Persisted next to the identity.
PIN pairing is optional and off by default; unpaired access still uses Noise
transport encryption.

## Music Assistant

**Player id** — Music Assistant's own id for the player fronting one of our
speakers. It is published, not derivable: MA lists our client id among that
player's output protocols, and the bridge learns the mapping when it connects.
Queue commands carry the learned id.

**Sync group** — a Music Assistant group of players sharing one queue. A
speaker in no sync group still has a queue of its own, addressed by its
player id.
