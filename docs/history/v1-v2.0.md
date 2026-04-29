# Project History

The architectural and functional evolution of sendspin-bt-bridge — from a single-file Bluetooth bridge to a multiroom audio platform for Home Assistant.

The full chronological development log has been moved to the [Journey Log](https://trudenboy.github.io/sendspin-bt-bridge/journey-log/) on the documentation site.

---

## Background: where the project came from

### loryanstrant/Sendspin-client — service created and published (January 1, 2026)

**January 1, 2026** Loryan Strant (Australia, AEDT +1100) created and published [SendspinClient](https://github.com/loryanstrant/Sendspin-client) — a Docker container bridging Music Assistant (via the Sendspin protocol) to a Bluetooth speaker on a Linux machine.

The motivation was entirely personal: an infrared sauna with Bluetooth speakers and a Surface Pro 4 running Ubuntu nearby — a familiar home-automation scenario. Loryan tried Squeezelite, but ESP32 + WiFi + A2DP gave an unstable connection. The idea was elegant: since MA can stream via Sendspin (WebSocket + FLAC/RAW) and the `sendspin` binary plays back to any PulseAudio device, all that's needed is a bridge in a container.

The original source was a single Python script, `sendspin` as a child process, `bluetoothctl` for connecting to the speaker, and a simple HTML status page. Loryan published the project in the MA Community thread [#4677](https://github.com/orgs/music-assistant/discussions/4677) and merged 4 PRs in a single day — improving BT disconnect detection and expanding the README.

### My involvement and the fork

I found thread #4677. My use case was similar — connecting Bluetooth speakers through Proxmox LXC, which loryanstrant's Docker approach didn't support: LXC containers have no access to AF_BLUETOOTH sockets due to kernel namespace restrictions.

**February 27, 2026** I left my first comment in the discussion describing the solution and submitted [PR #6](https://github.com/loryanstrant/Sendspin-client/pull/6) to the original repository: a new `lxc/` directory with `proxmox-create.sh` (runs on the PVE host — creates the LXC, bind-mounts the D-Bus socket, configures Bluetooth passthrough) and `install.sh` for setup inside the container. Tested on PVE 8.4.16 with a Sony WH-1000XM4.

**February 28, 2026** I published an extended fork, `sendspin-bt-bridge`, with fundamental new capabilities and asked Loryan in the same thread whether he minded me developing the project independently and publishing it as a standalone HA addon, with the commitment that he would always be credited as the founding author. New in the first fork release:

- **Multi-device**: multiple Bluetooth speakers simultaneously, each as a separate player in MA
- **Home Assistant addon** with Ingress (web UI in the HA sidebar without port forwarding)
- **`static_delay_ms`** — per-device A2DP latency compensation
- **`/api/diagnostics`** — structured healthcheck for adapters, sinks, and D-Bus
- **Audio format** in status (codec, sample rate, bit depth — e.g. `flac 48000Hz/24-bit/2ch`)
- **Volume persistence** per MAC in `LAST_VOLUMES` with automatic restore on reconnect

The explicit break from upstream was recorded in a commit dated March 1, 2026:
```
chore: detach from loryanstrant/Sendspin-client upstream
```

From that point the project develops entirely independently. The commit history from January 1 is inherited — loryanstrant's 14 commits remain part of the repository's git history.

---

### AI agents

The entire project was developed by a human in collaboration with AI agents — from architectural decisions and debugging through to documentation.

| AI agent | Role | Commits (Co-authored-by) |
|----------|------|--------------------------|
| **GitHub Copilot** (Claude Sonnet 4.6) | Primary working agent: refactoring, code, code review, documentation | ~540 |
| **Claude Code** (Anthropic, Claude Sonnet 4.6) | Architectural design, complex debugging, audio routing iterations | ~168 |

Copilot was used as an interactive CLI agent directly in the terminal (`gh copilot`); Claude Code was used for deep refactoring and diagnostic sessions. The phrase "with a certain AI buddy" in the first announcement in the MA discussion refers precisely to this workflow.

Some commits carry both tags simultaneously — in sessions where the solution was worked out in Claude Code and the final PR was reviewed in Copilot CLI.
