# Server texts and their codes

Decision record: UI/UX overhaul Q2. The bridge writes some texts for people
(guidance, recovery cards, header pill, action labels, timeline events, blocked
reasons, API errors). Each carries a stable `code` and `params`; the web UI
translates them (`server.<area>.<code>.<leaf>` in `ui/src/i18n/en.json` and
`ru.json`, through `ui/src/i18n/server.ts`). The English `title` / `summary` /
`label` / `message` / `detail` stay as the fallback for a code the UI does not
know. Words follow `docs/ux-glossary.md`.

The list of everything the UI must translate is `SERVER_TEXT` in
`src/sendspin_bridge/services/diagnostics/server_text.py`. After changing it run
`uv run python scripts/export_server_text_keys.py`; the vitest
`tests/unit/i18n/server-text.test.ts` then fails until English and Russian have
every key. Backend tests (`tests/unit/services/diagnostics/test_server_text.py`)
pin the codes, params and that a card's title and summary come from the same
state.

## What is covered

| Surface (where the SPA shows it) | API field(s) | Area | `code` is | Values in `params` |
|---|---|---|---|---|
| Guidance banner: issue groups | `bridge.guidance.issue_groups[]`: `title`, `summary` | `issue` | the issue key (`missing_sink`, `disconnected` ...) | `count`, `device`, `device_names`, plus per issue: `interval`, `socket`, `error`, `other_bridge`, `others`, `attempt`, `max_attempts`, `sink`, `port` |
| Diagnostics: recovery cards | `/diagnostics/recovery` `issues[]`: `title`, `summary` | `issue` | the issue key, or `setup_<step>` for a setup step | `device`, `count`, plus `error`, `cause_code`, `causes_codes`, `codec`, `adapter`, `config_dir`, `uid`, `remediation` ... |
| Header pill | `bridge.guidance.header_status.label` | `header`, or `issue` when `area` says so (the lead issue's title) | header code (`startup`, `issues_attention`, `devices_ready` ...) or the lead issue's code | `percent`, `count`, `ready`, `total`, or the issue's params |
| Action buttons (banner, recovery) | `primary_action` / `secondary_actions[]`: `label` | `action` | the action key, or a distinct code where one key has several labels (`release_bluetooth`, `reclaim_bluetooth`) | `count` (speakers named), `value` |
| Timeline events | `/diagnostics/timeline` `entries[]`: `label`, `summary`, `source` | `event`, `source` | the event type (`playback-started` ...), `startup-<phase>`, `health-<state>` | the event's details; `error`, `message`, `recovered` |
| Device events | `devices[].recent_events[]` | `event` | the event type | the event's details |
| Device health | `devices[].health`: `summary` | `event` as `health-<code>` | health code (`bluetooth_disconnected`, `error` ...) | `error`, `host`, `name` |
| Blocked controls ("Why?") | `devices[].capabilities.actions.*.blocked_reason` | `blocked` | `blocked_reason_detail.code` | none |
| API errors | problem+json `detail` | `problem` | the problem `code` (only the fixed-sentence ones) | none |

A card's summary can have a variant (`summary_variant`): the same title with
a different sentence for the same state (for example the bridge had no adapter
versus the Bluetooth service is down). The UI key is `summary_<variant>`.

## What stays English, and why

- Raw error text from the system (a speaker's `last_error`, connection errors):
  shown as a `{error}` value inside our sentence, never translated.
- Preflight and onboarding checklist text (`/diagnostics/onboarding`, check results
  from `/diagnostics/checks/*/run`, the guidance banner and onboarding card
  payloads) and the latency assistant's presets: the SPA does not show them, so
  they carry no codes yet.
- API errors whose `detail` names a value (an invalid adapter id, a missing
  field): the UI shows the server's `detail`.
- Log lines and the diagnostics report: technical text for support.
