# ManagedDevice unification (v0.5 plan)

**Status:** shipped — all three sequenced steps landed; see "Sequencing" below  
**Owner:** atlas backend  
**Replaces:** `IntuneDevice` table (which already holds cross-MDM rows)  
**Depends on:** —

## Why this exists

`IntuneDevice` was the original Microsoft Intune sync target back when atlas only spoke Intune. Today three other MDMs write to it (Jamf, Kandji, JumpCloud — see `app/services/{jamf,kandji,jumpcloud}_sync.py`) and the name is misleading both internally (developers ask "do we have a Jamf table?") and externally (table dumps in support tickets confuse customers who can see we read Jamf data).

The fields are also Microsoft-flavoured in ways that don't fit:

| Current field | Microsoft semantic | Cross-MDM reality |
|---|---|---|
| `enrolled_user_principal_name` | UPN (foo@tenant.onmicrosoft.com) | varies — Kandji has `user_email`, Jamf has `username`, JumpCloud has `email` |
| `aad_device_id` | Azure AD-specific guid | only populated for Intune; NULL for others |
| `compliance_state` | Intune-defined enum (`compliant` / `noncompliant` / `inGracePeriod`) | each MDM has its own enum — we currently coerce |
| `last_sync_to_intune_at` | when Intune saw it | misleading for Jamf/Kandji/JumpCloud rows |

Plus, with PR #64's `ExtensionDeviceHeartbeat` landing, we now have *two* device tables that need to be joined for a complete "managed and reporting" picture. The aggregate endpoint at `/endpoints/compliance` currently approximates this with the IntuneDevice-fresh-within-3-days heuristic, which is what PR #64 was foundational for replacing.

## What v0.5 will land

**A rename + schema clean-up, not a fork.** The existing rows stay where they are; we rename the table + columns + Python model to reflect cross-MDM reality.

### Schema changes

```sql
-- migration 018_managed_device_rename.py
ALTER TABLE grc.intune_devices RENAME TO managed_devices;

-- Microsoft-flavoured columns get neutral names:
ALTER TABLE grc.managed_devices RENAME COLUMN
  enrolled_user_principal_name TO enrolled_user_email;
ALTER TABLE grc.managed_devices RENAME COLUMN
  last_sync_to_intune_at TO last_sync_to_mdm_at;
ALTER TABLE grc.managed_devices RENAME COLUMN
  aad_device_id TO vendor_device_id;
-- compliance_state stays — the values are vendor-specific, the
-- column name is generic.
```

### Python changes

```python
# app/models/managed_device.py — replaces microsoft_sync_outputs.IntuneDevice
class ManagedDevice(GRCBase, TenantScopedMixin, TestDataMixin):
    __tablename__ = "managed_devices"
    # ... same columns, renamed
```

All four sync services (`intune_sync`, `jamf_sync`, `kandji_sync`, `jumpcloud_sync`) get a one-line import swap. Routers that touch the table likewise.

A backwards-compat alias stays in `microsoft_sync_outputs.py` for one release so existing imports don't break:
```python
from app.models.managed_device import ManagedDevice as IntuneDevice  # noqa
```

### Heartbeat join

`/endpoints/compliance.extension_installed_count` gets the swap PR #64 was foundational for:

```sql
SELECT COUNT(DISTINCT md.id)
FROM grc.managed_devices md
INNER JOIN grc.extension_device_heartbeats hb
  ON LOWER(md.enrolled_user_email) = hb.user_email
  AND md.tenant_id = hb.tenant_id
WHERE hb.last_seen_at > now() - interval '7 days'
  AND md.tenant_id = $tenant
GROUP BY md.id;
```

The 7-day window matches `EXTENSION_FRESH_WINDOW` in `extension.py`. Devices whose user has the extension *and* who reported in the past week count as "installed". The existing IntuneDevice-fresh-within-3-days heuristic stays as a fallback for tenants without the extension deployed.

## What v0.5 will NOT do

- **Add new device data shapes.** This is a rename, not an enrichment. Columns like `prompt_volume_24h_per_device` come from the extension heartbeat in v0.6.
- **Reshape `compliance_state` enums.** Each MDM has its own semantics; mapping them to a unified enum is a separate design exercise that touches downstream charts.
- **Migrate row data.** RENAME TABLE is metadata-only — instant on Postgres, no data movement.

## Migration risk

- **Concurrent reads during rollout.** Backwards-compat alias mitigates this for one release. After that release, the alias is removed and any straggler imports break loudly.
- **`extension_installed_count` shifts.** This is the desired effect, but it'll show as a non-zero swing in the dashboard the day the swap lands. Document in release notes.
- **Test fixture updates.** ~50 references in tests. One mass-edit PR.

## Sequencing

All three steps have shipped. Kept here as the record of how it was staged.

1. ~~**This PR (E):** ship the design doc + add a re-export alias in `app/models/managed_device.py`.~~ **Done.**
2. ~~**PR `feat/managed-device-rename` (v0.5 milestone):** the rename migration + Python model split + sync-service import swap + test fixture sweep.~~ **Done** — migration `018_managed_device_rename.py`, `app/models/managed_device.py`, with `IntuneDevice` left as a backwards-compat alias in `microsoft_sync_outputs.py`.
3. ~~**PR `feat/extension-heartbeat-compliance-join` (also v0.5):** swap `extension_installed_count` to use the join.~~ **Done** — `_is_extension_installed` in `app/routers/endpoints.py` joins `ExtensionDeviceHeartbeat` over `EXTENSION_FRESH_WINDOW`; the 3-day-fresh heuristic is gone.

The one piece of step 2 still outstanding is **removing the `IntuneDevice` alias**, which the doc scopes to "after one release".

## Open questions

- Should `vendor_device_id` be polymorphic-keyed (e.g. JSON `{intune: "...", jamf: "..."}`) or keep one column with the vendor implied by `integration_id`? Going with the latter for v0.5 — same shape as today, just renamed.
- Do we want a `device_kind` enum column (`workstation` / `mobile` / `server` / `virtualised`)? Useful for compliance scoring but not blocking — punt to v0.6.
