#!/usr/bin/env bash
# Capture frozen bluetoothctl transcripts for the FakeBluez corpora.
#
# Run on a host with a working Bluetooth stack (HAOS VM, LXC, test VM):
#
#     bash scripts/capture_bluez_transcripts.sh [output-root]
#
# Produces <output-root>/bluez-<ver>/ (default: ./bluez-capture) with the
# same file set as tests/support/transcripts/bluez-5.72/. Copy the
# directory into tests/support/transcripts/ to register the corpus, then
# wire it into tests/support/fake_bluez.py:_CORPORA.
#
# Captures use piped stdin on purpose — that is how the bridge drives
# bluetoothctl, and piped mode is what produces the banner / prompt-echo /
# ANSI shapes the parsers must survive.
set -euo pipefail

OUT_ROOT="${1:-./bluez-capture}"

VERSION="$(bluetoothctl --version | awk '{print $2}')"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: bluetoothctl --version returned nothing" >&2
    exit 1
fi

DEST="${OUT_ROOT%/}/bluez-${VERSION}"
mkdir -p "$DEST"

# Piped-session capture: commands on stdin, full raw stdout+stderr kept
# byte-for-byte (banner, ANSI escapes, prompt echoes, exit line).
capture() { # capture <outfile> <cmd> [cmd...]
    local outfile="$1"; shift
    { printf '%s\n' "$@"; printf 'exit\n'; } | bluetoothctl >"$DEST/$outfile" 2>&1 || true
}

# A MAC that is definitely not paired — exercises the "not available" shape.
MISSING_MAC="AA:BB:CC:DD:EE:FF"

bluetoothctl --version >"$DEST/version.txt" 2>&1
bluetoothctl list >"$DEST/list.txt" 2>&1
capture show.txt show
capture paired-devices.txt paired-devices
capture devices-paired.txt devices Paired
capture info-missing.txt "info $MISSING_MAC"

# First two paired devices, if any — real bonded-device shapes.
mapfile -t PAIRED < <(bluetoothctl devices Paired | awk '/^Device / {print $2}' | head -2)
if ((${#PAIRED[@]} >= 1)); then
    capture info-device-a.txt "info ${PAIRED[0]}"
fi
if ((${#PAIRED[@]} >= 2)); then
    capture info-device-b.txt "info ${PAIRED[1]}"
fi

# select + show against the first controller — captures the post-select
# prompt redraw and the hciN new_settings banner line.
FIRST_CTRL="$(bluetoothctl list | awk '/^Controller / {print $2; exit}')"
if [[ -n "$FIRST_CTRL" ]]; then
    capture select-show.txt "select $FIRST_CTRL" show
fi

# Host provenance (no secrets): kernel, distro, BlueZ package version.
{
    echo "captured_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host: $(uname -n)"
    echo "kernel: $(uname -r)"
    echo "os: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -s)"
    echo "bluetoothctl: $(bluetoothctl --version)"
    echo "paired_devices: ${PAIRED[*]:-none}"
} >"$DEST/PROVENANCE.txt"

echo "Captured to $DEST"
ls -la "$DEST"
echo
echo "Next: copy $DEST into tests/support/transcripts/ and register the"
echo "corpus in tests/support/fake_bluez.py (_CORPORA)."
