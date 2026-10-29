# Action policy

Every action the bridge UI offers has one of three shapes (decision Q3):

| Policy | When | How |
| --- | --- | --- |
| `undo` | reversible: Standby / Wake, Mute all, Power save, Enable / Disable, Release | run at once, a toast offers Undo: `undoable(id, forward, backward, ...)` |
| `confirm` | disruptive or irreversible: Remove, Forget bond, adapter power / reboot, Import, restart, update | `confirmAction(id, { title, consequences, confirmLabel })` opens `ConfirmConsequences` |
| `choice` | a question that is not destructive (keep or discard) | `confirmAction(id, ...)`, neutral button |

A confirm dialog names the consequences, one line each: which speakers, whether
playback stops, whether it can be undone. Offer a way out when there is one
(`link`, e.g. "Download current config first").

Long operations (scan, pairing, update, restart, measurement) show progress,
elapsed time, Cancel where the backend supports it, and an error with the next
step: `components/LongOperation.vue`.

Register a new action in `ACTION_POLICY` (`composables/useActionPolicy.ts`).
`tests/unit/policy/actionInventory.test.ts` fails for a raw `confirmDialog()`,
an unregistered id, a registry entry nobody uses, or a destructive button in a
file that never asks first.
