---
title: Group sync tuning
description: Line up the speakers of a Music Assistant sync group by ear, or measure them with a microphone or a phone and fine-tune by ear, and keep the results as profiles
---

Speakers in one Music Assistant sync group can play slightly apart when their hardware adds different latency (Bluetooth codecs, amplifiers, HDMI, sound servers). Sendspin compensates per player with a static output delay. The **Sync** screen sets that delay for every adjustable player of a group, bridge speakers and other Sendspin players alike.

## Open the screen

- **Music Assistant tab**: the **Sync** button on a group row.
- **A speaker's Timing tab**: **Tune with your group →** (for a player that belongs to a sync group). A player in no sync group gets an explanation of how to make one and a link to the Groups page.

Both open `/sync/<group id>`. Groups are Music Assistant's persistent sync groups, the ones the Groups page lists.

## What can be adjusted

The delay is stored in Music Assistant, as the player setting `sendspin_static_delay`. Music Assistant offers this setting only for Sendspin players whose client declares `set_output_delay`; bridge players do. Other members (Chromecast, AirPlay, DLNA, Sendspin clients without that command) are listed as **Cannot be adjusted** and are left out of every measurement.

- A delay is 0–5000 ms.
- **A larger delay makes a speaker play earlier.** Every delay control says so: *+ plays earlier · − plays later*. A speaker that sounds late gets more delay; one that sounds early gets less.
- Only differences matter. After a measurement the values are shifted so the smallest delay is 0.
- A delay above 2000 ms needs Music Assistant to send audio far ahead, and speakers with small buffers may drop out. The screen warns about it.

Writing a delay needs a Music Assistant token with administrator rights, because it changes player settings. With a token that lacks them the bridge shows the values to set by hand, with a **Copy values** button. You can instead reconnect Music Assistant with an administrator account in the bridge settings.

## Before you start

The **Group** tab lists the members with their delay (*Latency compensation (larger = earlier)*) and flags what would stop a measurement, before anything is touched:

- **Cannot be muted**: the player offers no mute in Music Assistant (web kiosks today). Hearing one speaker alone mutes all the others, so while it is in the group the other speakers cannot be measured one at a time. It can still be measured itself; or use **Manual tuning**, where **Everyone** and that speaker alone still work.
- **Unavailable**: Music Assistant reports the player switched off or disconnected.
- **Volume 0 %** and **Muted**: the speaker will not be heard. Turn it up or unmute it, or skip it.

**Skip … when measuring** marks a speaker to be skipped as soon as a measurement starts.

:::caution[Tuned before 3.1.0-beta.7? Tune again.]
Since 3.1.0-beta.7 the bridge takes the audio buffer's latency out of the schedule (**Compensate the audio buffer latency**, Settings → Audio, on by default), so its speakers play earlier by that latency: about 110 ms at `PULSE_LATENCY_MSEC` 200, 310 ms at 600 (the default) and 410 ms at 800 on PulseAudio 17. Delays tuned before absorbed it. The `Stream start timing:` log line shows the exact amount as `sink min`.
:::

## Two ways to tune

| | When to choose |
|---|---|
| **Manual tuning** | No microphone needed. Best for small fixes you can hear. |
| **Measure with a microphone** | About a minute per speaker. You hear and adjust the measured delays before you finish. |

The **Group** tab's **Manual tuning** and **Measure with a microphone** buttons are the way in; the **Manual** and **Measure** tabs link to each other. Opening a tab never starts anything: **Manual tuning** on the Group tab, or **Start tuning by ear** on the Manual tab, starts a tuning session, and the measurement wizard starts only from its own button.

### Manual tuning

Before you listen, the tab walks two preparation steps (*Before you listen (n/2)*): **Stop the music, start the clicks** and **Set the volume** (see below). Clicks that already play go straight to listening.

Listening controls:

- **Play clicks** starts the 3-minute click track on the whole group again from the start; *Clicks: 2:41 left* counts it down. **Stop clicks** stops only the click track (music someone started meanwhile plays on).
- **Only this speaker**: pick a speaker to hear it alone (the others are muted), or **Everyone**.
- **Compare two**: two speakers take turns every four seconds.
- **Sound**: what the clicks play — **Clicks**, **Clicks with accent** or **Noise** (see *Which sound to listen to*). Switching while it plays starts the new sound from the start; the speakers you muted or soloed stay that way.

Each member has a delay field, a slider and −10/−1/+1/+10 ms buttons. Changes are written to Music Assistant at once. **Put back the delays from the start** returns every changed speaker to where this tuning started.

#### Which sound to listen to

| Sound | What it is | When to use it |
|---|---|---|
| **Clicks** (default) | One short woodblock-like click a second | Bring every speaker to one hit |
| **Clicks with accent** | The same, every 4th click louder and higher | A speaker is far off (an echo of hundreds of ms), or you are not sure which click belongs with which |
| **Noise** | Steady pink noise (a soft hiss) | The last few milliseconds between two speakers |

How an offset sounds: up to about 1 ms the speakers sound as one, in the middle; 1–5 ms pulls the sound towards the earlier speaker; 5–30 ms smears it (a thick click, a hollow, "phasey" hiss); beyond 30–40 ms you hear two separate clicks, an echo. Sound covers about a third of a metre per millisecond, so every metre of difference between your distances to two speakers is ~3 ms: tune where you listen.

A good order: **Clicks with accent** while the speakers are far apart, **Clicks** until every speaker's click is one, then **Noise** for each pair of speakers (hear only the two, with **Only this speaker** or **Compare two**): stand midway and nudge 1 ms at a time until the hiss sits in the middle as one solid sound. While speakers are being measured (with a microphone or the phone) the measurement's own click track plays; the choice of sound returns once the measured delays are applied.

Leaving the tab finishes the tuning: the delays stay; the clicks stop, every speaker the bridge muted is unmuted and the volumes go back to what they were. If anything changed, the screen first offers to save the delays as a profile.

### Measure with a microphone

The wizard has four stages; the heading says where you are, for example *Prepare (1/4) · Stop the music, start the clicks*.

1. **Prepare**
   - **Where and how**: what would stop the measurement (see *Before you start*), where you measure (see below) and what records: **This device’s microphone** or **A phone (scan a QR code)**. *Takes about N minutes.* **Allow microphone and start** opens the microphone and starts the session (with a phone: **Start**). If the microphone cannot be used, the page offers **Manual tuning** or the phone.
   - **Stop the music, start the clicks**: one step. When the group plays something, the button reads **Stop the music and start the clicks**; a confirmation says that playback stops and the group's queue is replaced by the click track, and nothing is stopped before you agree. When the group is silent the button reads **Start the clicks**. The click track plays on every speaker of the group through Music Assistant, with a countdown. **Next** waits until the bridge sees the clicks playing and says so.
   - **Set the volume**: a volume slider per speaker, and the **Microphone** meter, which should jump with each click. A speaker at 0 % is flagged *Silent: turn it up to measure it*. *Volumes go back to what they were when you finish.*
2. **Measure: Speaker by speaker.** Each speaker plays alone while you record it (6 s at the speakers, 12 s from the listening position); the others are muted. A speaker shows **Heard**, **Not heard** or **Unstable**, with what to do: *Not heard: turn it up or move closer, then Repeat — or Skip it.* The order is only for your convenience; it does not change the result. If the clicks stopped, **Start the clicks again**; every speaker is then recorded once more. **Apply the measured delays and listen** opens once every speaker is recorded or skipped (the page says how many are left).
3. **Tune: Listen and adjust.** The measured delays are applied and the clicks keep playing. This is the same screen as Manual tuning: listen to each speaker alone or compare two, pick the **Sound** (the clicks change to the listening clicks here), and adjust any delay by hand. Each measured speaker shows *Measured: X ms (+Δ)* and **Reset to measured**; speakers not heard or skipped keep their delay. The page shows the largest gap between speakers as measured and what remains. **Finish** keeps the delays; **Finish and save as profile…** saves them first, with the gap in the notes. **Cancel** asks first, then puts back the delays from before the measurement.
4. **Done.** The group is restored. **Quick listen** plays clicks on the whole group for 10 s; **Measure again** starts over.

**Cancel** asks before recordings or applied delays are lost, and step 1 then says *Measurement cancelled. Speakers unmuted, volumes put back, clicks stopped.* After a page reload the screen says once that the tuning ended with it.

Where you measure:

- **At each speaker** (default): hold the phone 30–50 cm from the speaker being measured. It aligns the devices themselves and stays valid in any room.
- **From the listening position**: stay where you usually listen. It also includes the sound's travel to you, about 3 ms per metre. Save such a result as a profile of kind **From a listening position**.

### Measuring with a phone

Choose **A phone (scan a QR code)** on the first step. The speaker-by-speaker stage then shows a QR code and a link to a page on the bridge, made for phones. The phone does not need to sign in to the bridge; it must be on the same network as the bridge.

1. Scan the code on the phone and press **Start** there (or press **Play the sequence** on the computer while the phone records).
2. The bridge mutes every speaker for a moment, then plays each speaker alone (7 s at the speakers, 13 s from the listening position) with everyone muted in between (5 s to walk to the next speaker, 2 s from the listening position). The phone's page shows which speaker plays now and which is next.
3. When the sequence has finished, the phone sends the recording and the results appear on both screens. Skip a speaker or play the sequence again as needed (every speaker not skipped plays each time), then **Apply the measured delays and listen**.

How the phone records depends on the link:

- **The secure link (HTTPS)**, which the QR code uses when the bridge can serve it: the page records with the browser's microphone, and the recording stops and is sent by itself. The bridge serves only this page on its own port (the web port + 363, 8443 by default; set `SYNC_CAPTURE_HTTPS_PORT`, or `0` to turn it off) with a self-signed certificate kept in the config folder. Your phone will warn about the certificate once — accept it to record directly in the browser.
- **The plain link (`http://`)**: the browser cannot use the microphone, so **Start and open the recorder** opens the phone's own recorder. Press record in it at once; the bridge waits 10 s before the first speaker. A big countdown on the page says when to stop (the expected length is shown). The recording is sent when the recorder hands it back; **Send a recording from the phone** sends a file from a voice recorder app instead. The bridge reads m4a/AAC, 3GP, WAV, WebM/Opus, OGG, MP3, FLAC and AMR-WB; AMR-NB (phone-call quality) cuts off the clicks and is refused with a message saying so.

If the phone cannot do something (the clicks are not playing, the measurement is over, a recording cannot be read), it says what to do in words; the error code is only in small print.

The link carries a random token that only lets the phone see which speaker plays, start the sequence for this measurement and send its recording. It works for 30 minutes, a **New link** replaces it, and it stops working when the measurement ends.

## What you hear during a measurement

No music plays during a session. The group is stopped first (after you confirm); then the click track replaces the group's queue and plays on every speaker of the group (it shows as **Sendspin sync clicks**). When it ends the queue ends and the group stops, so no music comes back by itself. Calibration replaces the group's queue; start your music again afterwards. While one speaker is measured, the others are muted.

When the session ends (finished, cancelled, failed or timed out) the bridge puts the volumes it changed back to what they were (while the clicks still play), stops the click track, then unmutes every speaker it muted. Your music does not come back by itself: start it again in Music Assistant. The bridge never touches a speaker you muted yourself; a speaker you unmute yourself during the session is muted again by the next solo. Keep the room quiet and hold the phone still while it records.

## Speaker latency (air sensor)

With the experimental air sensor on (`EXPERIMENTAL_AIR_SENSOR`, a speaker with Playout sync set to `measure` playing SBC), a phone measurement also measures each bridge speaker's Bluetooth latency: how long the speaker takes from receiving the audio over Bluetooth to playing it. While a speaker plays alone in the sequence, the bridge records when its clicks left over the air; after the upload it subtracts that from the speaker's measured lateness. The result is stored per speaker, codec, sampling rate and Bluetooth adapter, applied at once, and shown in the device's **Timing** panel (with **Forget**). The wizard presents it as a result, not as a second delay: **Bluetooth latency measured: … ms**, and after saving **Bluetooth latency measured: … ms, saved**; it does not change the speaker's delay by itself.

It needs a reference: a member whose sound is on the Sendspin schedule, typically a Sendspin web player on a headset or a wired output. A phone recording has no clock link to the bridge, so a speaker's lateness can only be measured against another member; a reference that adds its own delay (PC speakers, a Bluetooth headset) makes every value wrong by that much. Pick it on the tuning step (**Reference**); without one no latency is offered.

With the air sensor switched off the tuning step shows none of this. With it on, every bridge speaker of the sequence shows either **Bluetooth latency measured: … ms** with the speaker's own delay report as a hint and **Save this Bluetooth latency** (then **Save**), or the reason there is none: the air sensor was not effective for it (for example Playout sync not set to `measure`), no reference, the speaker or the reference was not heard, it does not play SBC, its Bluetooth stream restarted during its turn, or its clicks were not found on the air or in what the bridge wrote. Measuring again and saving replaces the value for the same codec, rate and adapter. The live lock of the air sensor is not needed.

## Profiles

A profile is a named snapshot of a group's delays, kept in the bridge configuration (`SYNC_PROFILES`). Profiles are applied manually only; nothing selects one automatically.

- **Save current as…** stores the current delays with a name and optional notes. A profile saved after a measurement records the gap in its notes.
- A profile has a kind: **At the speakers** or **From a listening position**.
- **Apply** shows a table of current against profile values before anything is written. Members of the profile that are no longer in the group are listed and skipped.
- **Rename** and **Delete** change the profile only; the delays in Music Assistant stay as they are.

## Microphone requirements

Browsers allow microphone access only on secure pages: HTTPS or `localhost`. How you reach the bridge decides whether this device's microphone works:

- **Home Assistant ingress** is served over HTTPS (for example through your HA address), so it works.
- A plain `http://<lan-ip>:8080` page cannot use the microphone in most browsers. The wizard says so and offers to measure with a phone instead, which works over plain HTTP. Manual tuning does not need a microphone.

Other ways for a plain HTTP install:

- Open the bridge through Home Assistant ingress or another HTTPS reverse proxy.
- Put a reverse proxy with a certificate in front of the bridge (Caddy, nginx, Nginx Proxy Manager) and use that HTTPS address.
- Chrome and Edge only: add the bridge address to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and relaunch. This weakens the browser and applies to that browser only.
- Run the browser on the bridge host itself and open `http://localhost:8080`.

iOS needs a tap on the page to start recording, and a page sent to the background interrupts the recording; tap **Tap to continue** and the affected speakers are recorded again.

## Another tuning in progress

Only one tuning runs per group. If another device started a measurement, this page shows **Tuning in progress on another device** and is read-only. If another device has manual tuning open, the page shows **Manual tuning is open on another device** with **Join**; leaving this page never ends someone else's tuning. **End that session** unmutes the speakers the other device muted, puts its volumes back and stops its clicks; delays already written stay.

If the bridge restarts during a session, it puts the volumes back, unmutes the speakers that session muted and stops its click track if the group still plays it. While Music Assistant does not answer, the screen shows **Could not restore the group** with a **Retry** button; until then some speakers may still be muted.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A member is **Cannot be adjusted** | Its client does not declare `set_output_delay`, or it is not a Sendspin player. |
| Delays are not written, `ma_forbidden` | The Music Assistant token lacks administrator rights. |
| **Not heard** | Level too low, or phone too far; turn the speaker up or move closer and **Repeat**. |
| *… cannot be muted* | That player offers no mute in Music Assistant; skip the others or use Manual tuning, or take it out of the group. |
| **Stop the music and start the clicks** is refused | The group started playing something else meanwhile; press it again and confirm. |
| **Unstable** | Noise during the recording; repeat it in a quiet room. |
| This device cannot record | The page is not on HTTPS or `localhost`; measure with a phone, or see Microphone requirements. |
| The phone page says the link does not work | The link expired (30 min), a new link replaced it, or the measurement ended; open a **New link**. |
| The phone's recording does not stop by itself | The page was opened over plain `http://` and uses the phone's recorder: stop it when the countdown reaches 0, or open the secure link. |
