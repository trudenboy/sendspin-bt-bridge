---
title: Group sync tuning
description: Line up the speakers of a Music Assistant sync group by ear or with the phone microphone, and keep the results as profiles
---

Speakers in one Music Assistant sync group can play slightly apart when their hardware adds different latency (Bluetooth codecs, amplifiers, HDMI, sound servers). Sendspin compensates per player with a static output delay. The **Sync** screen sets that delay for every adjustable player of a group, bridge speakers and other Sendspin players alike.

## Open the screen

- **Music Assistant tab**: the **Sync** button on a group row.
- **Device card or row**, timing section: **Tune the whole group** (shown for players that belong to a sync group).

Both open `/sync/<group id>`. Groups are Music Assistant's persistent sync groups, the ones the Groups page lists.

## What can be adjusted

The delay is stored in Music Assistant, as the player setting `sendspin_static_delay`. Music Assistant offers this setting only for Sendspin players whose client declares `set_output_delay`; bridge players do. Other members (Chromecast, AirPlay, DLNA, Sendspin clients without that command) are listed as **Cannot be adjusted** and are left out of every measurement and proposal.

- A delay is 0–5000 ms.
- A **larger delay makes a speaker play earlier**. A speaker that sounds late gets more delay; one that sounds early gets less.
- Only differences matter. After a measurement the values are shifted so the smallest delay is 0.
- A delay above 2000 ms needs Music Assistant to send audio far ahead, and speakers with small buffers may drop out. The proposal warns about it.

Writing a delay needs a Music Assistant token with administrator rights, because it changes player settings. With a token that lacks them the bridge shows the values to set by hand, with a **Copy values** button. You can instead reconnect Music Assistant with an administrator account in the bridge settings.

## Tuning modes

### Manual tuning

Choose **Manual tuning**. Each member has a slider and ±1 ms and ±10 ms nudge buttons. Changes are written to Music Assistant at once, and the slider in Music Assistant follows.

To judge by ear, use the **Listen** controls:

- **Play clicks** plays a click track on the whole group; **Stop clicks** ends it.
- **Solo** plays the clicks on one member only.
- **A/B** alternates two members every two clicks.

**Reset to session start** puts the delays back to what they were when you opened manual tuning. When you leave, the screen offers to save the delays as a profile.

### Measure and suggest

Choose **Measure and suggest** to measure with the microphone of the device that shows the page, review the proposal, then apply it. Steps of the wizard:

1. **Preparation**. Choose where you measure (see below) and allow the microphone. Recordings stay in memory and are sent only to this bridge.
2. **Level check**. Clicks play on every speaker; the meter must move with each click and the screen says whether the level is too quiet, too loud or fine.
3. **Speaker by speaker**. Each member plays alone while you record it (6 s at the speakers, 12 s from the listening position). You can reorder the speakers, repeat a step or skip a speaker. A member shows **Heard**, **Not heard** or **Unstable**.
4. **Proposal**. A table of before, after and change per speaker, and the spread before and the predicted spread after. Warnings cover delays clamped to 0–5000 ms, delays above 2000 ms, and members that could not be verified. Computing the proposal does not change anything.
5. **Done**. **Apply**, or **Apply and save as profile…**. **Check again after applying** measures every speaker once more with the new delays. **Quick listen** plays clicks on the whole group for 10 s.

The result does not depend on which speaker is measured first, so the wizard has no reference speaker to choose.

Where you measure:

- **At each speaker** (default): hold the phone 30–50 cm from the speaker being measured. It aligns the devices themselves and stays valid in any room.
- **From the listening position**: stay where you usually listen. It also includes the sound's travel to you, about 3 ms per metre. Save such a result as a profile of kind **From a listening position**.

### Measure and apply

Choose **Measure and apply** to measure, apply and verify in one run. The result shows the spread before and after. **Undo** stays available for 30 s and puts the earlier delays back.

## What you hear during a measurement

Playback of the group is interrupted for the session. The group plays a click track, and members are muted one by one so each is measured alone. When the session ends (done, cancelled, failed or timed out) the bridge stops the test signal, unmutes what it muted and restores what was playing. Keep the room quiet and hold the phone still while it records.

## Profiles

A profile is a named snapshot of a group's delays, kept in the bridge configuration (`SYNC_PROFILES`). Profiles are applied manually only; nothing selects one automatically.

- **Save current as…** stores the current delays with a name and optional notes. A profile saved after a measurement records the spread in its notes.
- A profile has a kind: **At the speakers** or **From a listening position**.
- **Apply** shows a table of current against profile values before anything is written. Members of the profile that are no longer in the group are listed and skipped.
- **Rename** and **Delete** change the profile only; the delays in Music Assistant stay as they are.

## Microphone requirements

Browsers allow microphone access only on secure pages: HTTPS or `localhost`. How you reach the bridge decides whether measuring works:

- **Home Assistant ingress** is served over HTTPS (for example through your HA address), so it works.
- A plain `http://<lan-ip>:8080` page cannot use the microphone in most browsers. The wizard says so (`This browser cannot record here`). Manual tuning does not need the microphone and works anyway.

Workarounds for a plain HTTP install:

- Open the bridge through Home Assistant ingress or another HTTPS reverse proxy.
- Put a reverse proxy with a certificate in front of the bridge (Caddy, nginx, Nginx Proxy Manager) and use that HTTPS address on the phone.
- Chrome and Edge only: add the bridge address to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and relaunch. This weakens the browser and applies to that browser only.
- Run the browser on the bridge host itself and open `http://localhost:8080`.

iOS needs a tap on the page to start recording, and a page sent to the background interrupts the recording; use **Continue recording** and the affected steps are measured again.

## Another tuning in progress

Only one tuning runs per group. If another device started one, this page shows **Tuning in progress on another device** and is read-only. **End that session** stops the other device's test signal and restores the group; delays already written stay.

If the bridge restarts during a session, it keeps trying to restore the group, and the screen shows **Could not restore the group** with a **Retry** button until Music Assistant answers. Until then some speakers may still be muted or playing the test signal.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A member is **Cannot be adjusted** | Its client does not declare `set_output_delay`, or it is not a Sendspin player. |
| Delays are not written, `ma_forbidden` | The Music Assistant token lacks administrator rights. |
| **Not heard** | Level too low, or phone too far; repeat the step closer or turn the group up. |
| **Unstable** | Noise during the recording; repeat it in a quiet room. |
| The wizard cannot record | Page is not on HTTPS or `localhost`; see Microphone requirements. |
