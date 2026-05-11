# MQTT `entity_category` validation regression — implementation plan

> **For Claude:** Use superpowers:executing-plans for task-by-task execution.

**Goal:** Stop shipping the non-standard `entity_category="sendspin_bridge.config"`
value in HA MQTT discovery; HA Core 2026.5.x rejects it with
`Error 'expected EntityCategory or one of 'config', 'diagnostic''`.

**Architecture:** One-line value change in 8 `EntitySpec` rows + a regression
test that fails the suite if anyone re-adds an out-of-enum value. The string
`"sendspin_bridge.config"` is also reused as an internal `availability_class`
bucket — that usage is unrelated to HA's enum and stays.

**Tech Stack:** Python 3.13, pytest, paho-mqtt; HA MQTT discovery schema.

---

## Task 1: Regression test (RED)

**Files:**
- Modify: `tests/unit/services/ha/test_ha_entity_model.py`

Append a parametrised test that asserts every `EntitySpec.entity_category`
is one of `{None, "config", "diagnostic"}`. The set comes from
`homeassistant.helpers.entity.EntityCategory` (we hard-code the values
rather than import HA — same approach used by `device_class` checks).

```python
@pytest.mark.parametrize(
    "spec", [*M.DEVICE_ENTITIES, *M.BRIDGE_ENTITIES], ids=lambda s: s.object_id
)
def test_entity_category_matches_ha_enum(spec):
    """HA Core 2026.5.x strictly validates entity_category against the
    EntityCategory enum. Custom values like 'sendspin_bridge.config' cause
    an MQTT discovery rejection every publish cycle and break HA dashboards
    (issue from community forum post #134, 2026-05-10)."""
    assert spec.entity_category in (None, "config", "diagnostic"), (
        f"{spec.object_id}: entity_category={spec.entity_category!r} — must be "
        f"None, 'config', or 'diagnostic' to satisfy HA's EntityCategory enum"
    )
```

Run: `uv run pytest tests/unit/services/ha/test_ha_entity_model.py -v -k entity_category`
Expected: 8 FAIL (the 8 spec rows still on `sendspin_bridge.config`).

## Task 2: Fix the 8 specs (GREEN)

**Files:**
- Modify: `src/sendspin_bridge/services/ha/ha_entity_model.py` (8 lines + 1 doc-comment)

Replace `entity_category="sendspin_bridge.config"` with `entity_category="config"`
on lines 392, 402, 421, 435, 445, 456, 467, 481. These are exactly the entities
HA rejected per the production log (Enabled, BT management, Standby, Power save,
Idle mode, Keep-alive method, Static delay, Power save delay).

Also fix the misleading type-hint comment on line 81:

```python
entity_category: str | None = None  # "diagnostic" | "config" | None  (HA EntityCategory)
```

Leave `availability_class="sendspin_bridge.config"` untouched — it's an
internal bucket name, not sent to HA, and renaming would mean touching
~25 lines plus `AVAILABILITY_CLASSES`. Out of scope.

Run: `uv run pytest tests/unit/services/ha/test_ha_entity_model.py -v`
Expected: GREEN, including the new entity_category test.

## Task 3: Regression sweep

Run: `uv run pytest -q`
Expected: full suite green (no test relied on the wrong value).

## Task 4: CHANGELOG + commit

**Files:**
- Modify: `CHANGELOG.md` — one `### Fixed` entry under `[Unreleased]`.

Lint: `uv run python scripts/lint_changelog.py CHANGELOG.md` → clean.

Commit + push.

## Task 5: Hot-verify on prod (HAOS VM 104)

The deployed addon is on v2.69.0 (ghcr image already built). To verify
the fix in production *before* cutting a release:

1. Copy patched `ha_entity_model.py` into the running addon container:

   ```bash
   cat src/sendspin_bridge/services/ha/ha_entity_model.py | \
     ssh proxmox "qm guest exec 104 --pass-stdin -- bash -c \
       'cat > /tmp/ha_entity_model.py'"
   ssh proxmox "qm guest exec 104 -- bash -c \
     'docker cp /tmp/ha_entity_model.py \
       addon_85b1ecde_sendspin_bt_bridge:/app/src/sendspin_bridge/services/ha/ha_entity_model.py \
      && docker restart addon_85b1ecde_sendspin_bt_bridge'"
   ```

2. Wait ~30 s for first MQTT discovery cycle, then:

   ```bash
   ssh haos "ha core logs 2>&1 | tail -200 | grep -iE 'entity_category|sendspin'"
   ```

   Expected: no new `Error 'expected EntityCategory'` lines after the
   restart timestamp. Older lines in the log are historical.

## Task 6: Decide on release (defer to user)

VERSION bump triggers `release.yml` → tag + image build + GH release.
For a hard production-breaking bug (forum reports, errors every 30 s on
every user with HA 2026.5+), a patch release `2.69.1` is appropriate.
Do not bump VERSION without explicit user approval.

---

## Risk

- **Existing HA dashboards.** Renaming `entity_category` from `sendspin_bridge.config`
  to `config` may move entities from the "main" group into the "Configuration"
  collapsible section on the device card. That is the *intended* HA behavior
  for config knobs; users who relied on always-visible position will need to
  expand the section. Document this in the CHANGELOG entry.
- **No data migration needed.** `entity_category` is a discovery-payload field,
  not stored state. HA picks up the new value on the next discovery message.
