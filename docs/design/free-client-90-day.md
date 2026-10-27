# The free Work client's 90-day rule: enforcing it in Atlas

Promptly Guide's Work client is free. Its issue
[promptly-guide#52](https://github.com/Prompt-Shields/promptly-guide/issues/52) adds one
rule: "After 90 days without a paid module, the customer must connect its own model
endpoint", enforced in the backend. Atlas already works out where an organisation stands
on that rule. This note covers the part still missing, which is Atlas acting on it, and
what the owner has to decide first.

Status: **proposal, nothing built.** The behaviour waits on the owner's decision B4 in
promptly-guide's `docs/decisions-needed.md` ("what stops at day 90?"), with B5 next to it.

## What exists today

- **The status.** `app/services/entitlements.py`, `free_client_grace()`, works out one of
  three statuses for an organisation whose Work client has started
  (`tenants.work_client_started_at`):
  - `active`: inside the 90 days, or exempt because it pays for a module or has declared its
    own endpoint (`tenants.own_endpoint_declared_at`);
  - `grace_ending`: the last days of the 90, so admins can be warned;
  - `endpoint_required`: day 90 has passed, with no paid module and no declared endpoint.

  It returns no status when the Work client has not started. The comment above it says
  that nothing there enforces the rule.
- **Who reads it.** `GET /billing/entitlements` and the console show it to admins.
  `GET /guide/work-client` (`app/routers/guide.py`) hands it to Guide with the days left.
  The conversion report (`app/services/conversion_report.py`) counts it.
- **Guide's side.** promptly-guide PR #252 (`Sources/GuideCore/WorkClientGrace.swift`)
  built B4's recommended option as an assumption, which a later "no" can still undo. At
  `endpoint_required`, Guide refuses only the hosted route. Answers on the Mac and from
  the organisation's own endpoint keep working.
- **The hosted step meter.** `app/services/guided_step_meter.py` gives the future hosted
  step gateway its verdict. The gateway calls `check()` before it serves a hosted step
  and `record()` after. `admit()` refuses a step when the organisation has no Work
  client, has its own endpoint, has no pool configured, or has used its pool with the
  hard cap on. It does not read the 90-day status.

Guide's refusal is a courtesy to the person using it. Atlas is where the rule binds,
because the gateway asks Atlas before it serves a step, whatever the client does.

## The options

What should happen when an organisation in `endpoint_required` asks for a hosted step?

| Option | What happens at `endpoint_required` | Notes |
| --- | --- | --- |
| **A. Refuse hosted steps** | `admit()` refuses with a new reason. Guide keeps working on the Mac and with the organisation's own endpoint. | B4's recommended option. Matches what Guide already does (#252) and what `GET /guide/work-client` tells admins. |
| B. Warn only | Steps are still served. Guide and the console keep showing the notice. | Does not meet "enforced in the backend". The hosted model stays an open cost. |
| C. Slow down | Steps are served at a reduced rate or from a smaller pool. | Needs a rate or a pool size that nobody has decided. It is also a softer form of A that admins may read as a fault. |
| D. Pay per step | Steps are served and every one is metered as overage. | This is decision B5, which is open with no recommendation. The overage path to Stripe (`billing/step_overage.py`) already exists. |
| E. Stop the whole client | Guide stops, on the Mac too. | B4's other option. Atlas cannot enforce this alone: answers on the Mac never reach it. |

## Recommendation

**Option A**, once the owner confirms B4. It is the narrowest rule that meets "enforced in
the backend". It only stops something Promptly pays for, and Guide's refusal and the
admin notices already promise this behaviour. If B5 later says yes, option D can be added
on top of A: the refusal becomes "serve and meter" for organisations that opted in.

Fail closed. A step is refused whenever the status is `endpoint_required`.

Nothing else changes:

- An organisation with a declared endpoint is already refused hosted steps with
  `own_endpoint`, as today. The new reason never applies to it, because
  `free_client_grace()` gives it `active`.
- An organisation that pays for a module is `active` and is served as today.
- A pilot follows the same rules as any other customer. `free_client_grace()` does not
  treat pilots as a special case, and neither should enforcement. A pilot that should
  keep hosted steps past day 90 needs a paid module or a declared endpoint, or a
  decision from the owner to exempt pilots.

## Where it hooks in

`check()` in `app/services/guided_step_meter.py` is the gateway's only entry point. Today
it calls `admit(await pool_status(...))`. The change:

1. In `guided_step_meter.py`, add `Refusal.ENDPOINT_REQUIRED = "endpoint_required"`.
2. `admit(status, *, free_client)` takes the 90-day status as a **required** keyword
   argument (`FreeClientStatus | None`). A caller cannot leave it out by mistake, and
   `admit()` stays pure. The order of the checks:
   1. `no_work_client`;
   2. `own_endpoint`;
   3. **`endpoint_required`**: refused, `meter_step=False`, `remaining=0`;
   4. `pool_unknown`;
   5. `pool_used`.

   It comes after `own_endpoint`, so an organisation with its own endpoint keeps its
   current answer. It comes before the pool checks, so day 90 refuses even when the pool
   is unknown or has room.
3. `check(db, tenant, *, now)` works out
   `entitlements.free_client_grace(tenant, now=now).status` and passes it on. `now` is the
   DB server's clock, as `check()` already requires.
4. `PoolStatus`, `month_status()`, `pool_status()` and `record()` stay as they are. Billing
   (`step_overage.enqueue_overage`) and the admin's month view (`GET /billing/guided-steps`)
   do not change. A refused step is never recorded, so it is never billed.
5. Update the comments: in `entitlements.py`, the comment above `free_client_grace()` should
   say the meter now enforces the status; in `guided_step_meter.py`, list the new refusal
   in the module docstring.
6. When the gateway is built, it passes the refusal on to Guide as its own code.
   `contracts/atlas-guide.json` gains that code, on both sides together. Guide already
   treats the hosted route as refused at this status, so the code only has to be
   recognised.

No migration and no new route. Nothing new is stored or sent, and nothing about a person
is read (E4).

## Tests

In `backend/tests/unit/test_guided_step_meter.py`, as pure tests of `admit()`:

- `free_client=None` (not started), `active` and `grace_ending`: the verdict is the same as
  today's for each pool state (pool unknown, room left, used with the hard cap on and off).
- `endpoint_required`: refused with `endpoint_required`, `meter_step` false, `remaining` 0.
  This holds with room in the pool, with the pool used, with the hard cap off, and with
  the pool unknown.
- `endpoint_required` with no Work client: `no_work_client` still wins.
- An own endpoint: `own_endpoint` whatever the 90-day status.

In the same file, as `check()` tests against the database:

- A Work client started more than 90 days before the DB's `now()`, with no paid module and
  no endpoint: refused with `endpoint_required`.
- The same organisation one day inside the 90: served.
- The boundary: `now` exactly at day 90 is `endpoint_required`, matching `free_client_grace()`.
- After day 90 with a paid module (a module subscription that is active): served.
- After day 90 with a declared endpoint: `own_endpoint`.
- After day 90, a pilot Work client subscription (`promptly_pilot` set) with no paid module:
  refused, the same as for a customer that is not a pilot.
- A module subscription that is past due counts as no paid module: refused.

Elsewhere:

- In `record()`: a report for an organisation in `endpoint_required` is still counted. The
  step was already served, and `check()` is the gate.
- In `test_step_overage.py`: the overage for the month is unchanged by the status.
- A contract test for the new refusal code, once the gateway's response is in
  `contracts/atlas-guide.json`.

## What the owner decides

1. **B4.** After day 90, does an organisation lose the hosted steps only (option A,
   recommended), or the whole client (option E)?
2. **B5.** May an organisation in `endpoint_required` pay per hosted step instead (option D)?
3. **Pilots.** Do they follow the same rule (recommended), or are they exempt until the
   pilot ends?
4. **When to build it.** The hosted step gateway does not exist yet, so no hosted step is
   served today. Option A can go in before the gateway or together with it.
