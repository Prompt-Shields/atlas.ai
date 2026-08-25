"""Slack platform adapter — signing, Block Kit, payload parsing, API calls.

Split into two halves:

  - Pure helpers (no I/O): signing-secret verification, Block Kit
    serialisation for each question type, interaction-payload
    parsing. These get the bulk of the unit tests because they're
    trivially testable without a Slack workspace.

  - Slack API calls (httpx): chat.postMessage, conversations.open,
    users.lookupByEmail, chat.delete. Each takes an httpx.AsyncClient
    parameter so tests can pass a mock transport.

The PR C goal is to make the Slack-bot delivery path complete and
unit-tested; the actual end-to-end flow against a real Slack
workspace happens in the design-partner private beta (week 4 of the
build plan).

Block Kit references:
  https://api.slack.com/block-kit
  https://api.slack.com/reference/interaction-payloads/block-actions

Signing-secret verification:
  https://api.slack.com/authentication/verifying-requests-from-slack
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

# ─── Signing-secret verification ─────────────────────────────────────


# Slack's recommended window is 5 minutes. Older = replay attack risk.
SIGNATURE_FRESHNESS_SECONDS = 5 * 60


class SignatureInvalid(Exception):
    """Raised when an inbound webhook signature fails verification."""


def verify_signature(
    signing_secret: str,
    timestamp_header: str,
    body: bytes,
    signature_header: str,
    *,
    now: float | None = None,
) -> None:
    """Verify a Slack webhook's `X-Slack-Signature` header.

    Raises `SignatureInvalid` on any failure mode:
      - missing/malformed headers
      - stale timestamp (replay attack window)
      - HMAC mismatch

    `now` is injectable for tests; defaults to real time.
    """
    if not signing_secret:
        raise SignatureInvalid("signing secret not configured")
    if not signature_header or not signature_header.startswith("v0="):
        raise SignatureInvalid("missing or malformed X-Slack-Signature")
    try:
        ts = int(timestamp_header)
    except (TypeError, ValueError) as exc:
        raise SignatureInvalid("X-Slack-Request-Timestamp must be an integer") from exc

    now_ts = now if now is not None else time.time()
    if abs(now_ts - ts) > SIGNATURE_FRESHNESS_SECONDS:
        raise SignatureInvalid("X-Slack-Request-Timestamp outside freshness window")

    base = f"v0:{timestamp_header}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise SignatureInvalid("X-Slack-Signature mismatch")


# ─── Block Kit rendering ─────────────────────────────────────────────


CONSENT_HEADER = (
    "Hi! I'm *Atlas* — your IT lead at {tenant_name} is collecting how the "
    "team uses AI tools for compliance. Your responses go to your IT lead "
    "only. Reply *STOP* to opt out forever."
)


def render_consent_block(tenant_name: str) -> dict[str, Any]:
    """The opening section of every initial DM. Required by §6 of the bot doc."""
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": CONSENT_HEADER.format(tenant_name=tenant_name),
        },
    }


def render_progress_block(answered: int, total: int) -> dict[str, Any]:
    """Tiny progress hint above the question (e.g. 'Question 2 of 5')."""
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Question *{answered + 1}* of *{total}*",
            }
        ],
    }


def render_question_blocks(
    *,
    question: dict[str, Any],
    response_id: str,
    answered: int,
    total: int,
) -> list[dict[str, Any]]:
    """Translate a survey_flow Question (dict form) into Block Kit blocks.

    The shape switches on `question["type"]`:

      - singleSelect → radio_buttons + Submit button
      - yesNo        → static_select (same as singleSelect for now)
      - multiSelect  → checkboxes + Submit button
      - freeText     → plain_text_input + Submit (or Skip when optional)

    Action ids encode `{response_id}::{question_id}` so the
    interactions webhook can recover both without a state cookie.
    """
    qtype = question["type"]
    qid = question["id"]
    prompt = question["prompt"]
    options = question.get("options", [])
    optional = bool(question.get("optional", False))
    action_id = f"{response_id}::{qid}"

    blocks: list[dict[str, Any]] = [
        render_progress_block(answered, total),
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{prompt}*"},
        },
    ]

    if qtype in ("singleSelect", "yesNo"):
        blocks.append(
            {
                "type": "actions",
                "block_id": f"block-{qid}",
                "elements": [
                    {
                        "type": "radio_buttons",
                        "action_id": action_id,
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": opt},
                                "value": opt,
                            }
                            for opt in options
                        ],
                    }
                ],
            }
        )
    elif qtype == "multiSelect":
        blocks.append(
            {
                "type": "actions",
                "block_id": f"block-{qid}",
                "elements": [
                    {
                        "type": "checkboxes",
                        "action_id": action_id,
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": opt},
                                "value": opt,
                            }
                            for opt in options
                        ],
                    }
                ],
            }
        )
    elif qtype == "freeText":
        blocks.append(
            {
                "type": "input",
                "block_id": f"block-{qid}",
                "optional": optional,
                "label": {"type": "plain_text", "text": " "},
                "element": {
                    "type": "plain_text_input",
                    "action_id": action_id,
                    "multiline": True,
                    "max_length": 2000,
                },
            }
        )
    else:
        raise ValueError(f"unsupported question type for Block Kit: {qtype}")

    # Submit + Skip (skip only when optional).
    submit_elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": f"submit::{response_id}::{qid}",
            "text": {"type": "plain_text", "text": "Submit"},
            "style": "primary",
            "value": qid,
        }
    ]
    if optional:
        submit_elements.append(
            {
                "type": "button",
                "action_id": f"skip::{response_id}::{qid}",
                "text": {"type": "plain_text", "text": "Skip"},
                "value": qid,
            }
        )
    blocks.append({"type": "actions", "elements": submit_elements})
    return blocks


def render_thanks_block(new_use_case: bool) -> list[dict[str, Any]]:
    """The closing message after the last question."""
    body = (
        "✅ Thanks — your IT lead can see this and will review. We'll create "
        "a use case entry for you to confirm."
        if new_use_case
        else "✅ Thanks for the response. Nothing to register from this one."
    )
    return [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]


# ─── Interaction payload parsing ─────────────────────────────────────


class InteractionParseError(Exception):
    """Raised when the Block Kit interaction payload doesn't match expected shape."""


def parse_interaction(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the relevant fields out of a Block Kit interaction payload.

    Slack POSTs interactions as form-encoded with `payload=<json>`. The
    router decodes that and hands the JSON object here. We extract:

      action_type:  "submit" | "skip" | "answer_select"
      response_id:  the SurveyResponse uuid as a string
      question_id:  the question id from the template
      value:        for submit, the user's answer in its native shape:
                      - radio_buttons → str (selected option value)
                      - checkboxes    → list[str]
                      - plain_text    → str
                    for skip → None

    Raises `InteractionParseError` on missing fields. We do *not*
    validate the value against the template here — that's
    `survey_flow.validate_answer` in the router.
    """
    actions = payload.get("actions") or []
    if not actions or not isinstance(actions, list):
        raise InteractionParseError("payload.actions[] missing or empty")

    action = actions[0]
    raw_id = action.get("action_id") or ""
    if "::" not in raw_id:
        raise InteractionParseError(f"action_id missing :: separator (got {raw_id!r})")

    head, rest = raw_id.split("::", 1)
    if head in {"submit", "skip"}:
        if "::" not in rest:
            raise InteractionParseError(
                f"submit/skip action_id missing response_id::question_id (got {raw_id!r})"
            )
        response_id, question_id = rest.split("::", 1)
        action_type = head
    else:
        # head is the response_id, rest is the question_id (the
        # element-level action_id, fired on every radio/checkbox click).
        response_id = head
        question_id = rest
        action_type = "answer_select"

    if action_type == "skip":
        value: Any = None
    else:
        value = _extract_value(action, payload)

    return {
        "action_type": action_type,
        "response_id": response_id,
        "question_id": question_id,
        "value": value,
        "user_id": (payload.get("user") or {}).get("id"),
        "team_id": (payload.get("team") or {}).get("id"),
    }


# ─── Inline reminder-action parsing ──────────────────────────────────
#
# Separate from parse_interaction() above (which is survey-specific).
# Reminder buttons use a `rmd_<verb>::<target>` action_id namespace so
# the router can branch cleanly before touching survey state.
#
# Verbs:
#   rmd_review_done       — mark a UseCaseReview as REVIEWED (no drift)
#   rmd_review_snooze     — bump last_reminder_sent_at by 24h
#   rmd_handbook_ack      — create HandbookAcknowledgement
#   rmd_handbook_snooze   — log a HandbookReminderLog stamped now+24h
#                           so the dispatcher cooldown skips an extra cycle
#
# Targets:
#   review_done / review_snooze    → review_id (uuid string)
#   handbook_ack / handbook_snooze → handbook version (e.g. "1.0")


REMINDER_ACTION_PREFIX = "rmd_"


def is_reminder_action(payload: dict[str, Any]) -> bool:
    """Cheap peek used by the router to branch before full parsing."""
    actions = payload.get("actions") or []
    if not actions:
        return False
    aid = (actions[0] or {}).get("action_id") or ""
    return aid.startswith(REMINDER_ACTION_PREFIX)


def parse_reminder_interaction(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the relevant fields out of a reminder-button interaction.

    Returns:
      verb:           one of the rmd_* verbs above (without the prefix —
                      e.g. "review_done")
      target:         the review_id or handbook version, as a string
      slack_user_id:  the clicker (the *actor* — we re-resolve to a
                      User row via users.info in the handler)
      team_id:        slack workspace id
      response_url:   single-use URL we POST the confirmation to. Slack
                      gives us 3s to return synchronously *and* up to 30
                      minutes via response_url for an updated message.

    Does not perform DB writes or authorization — pure parsing.
    """
    actions = payload.get("actions") or []
    if not actions or not isinstance(actions, list):
        raise InteractionParseError("payload.actions[] missing or empty")

    raw_id = (actions[0] or {}).get("action_id") or ""
    if not raw_id.startswith(REMINDER_ACTION_PREFIX):
        raise InteractionParseError(f"action_id is not a reminder action (got {raw_id!r})")
    if "::" not in raw_id:
        raise InteractionParseError(f"reminder action_id missing :: separator (got {raw_id!r})")

    head, target = raw_id.split("::", 1)
    verb = head[len(REMINDER_ACTION_PREFIX) :]  # strip "rmd_"

    if verb not in {
        "review_done",
        "review_snooze",
        "handbook_ack",
        "handbook_snooze",
    }:
        raise InteractionParseError(f"unknown reminder verb {verb!r}")

    if not target:
        raise InteractionParseError(f"reminder action_id missing target (got {raw_id!r})")

    return {
        "verb": verb,
        "target": target,
        "slack_user_id": (payload.get("user") or {}).get("id") or "",
        "team_id": (payload.get("team") or {}).get("id") or "",
        "response_url": payload.get("response_url") or "",
    }


def _extract_value(action: dict[str, Any], payload: dict[str, Any]) -> Any:
    """Return the user's selected value(s) for a Block Kit element."""
    atype = action.get("type")

    # radio_buttons → `selected_option.value` (string)
    if atype == "radio_buttons":
        chosen = action.get("selected_option") or {}
        return chosen.get("value")

    # checkboxes → `selected_options[].value`
    if atype == "checkboxes":
        return [opt.get("value") for opt in (action.get("selected_options") or [])]

    # static_select → `selected_option.value`
    if atype == "static_select":
        chosen = action.get("selected_option") or {}
        return chosen.get("value")

    # plain_text_input button submit: value is the input field's
    # current state, found under payload.state.values
    if atype == "button":
        state = (payload.get("state") or {}).get("values") or {}
        # button.value is the question_id; the input block is named
        # block-<qid>, and inside it is the action_id we set.
        qid = action.get("value")
        block = state.get(f"block-{qid}") or {}
        for el in block.values():
            if isinstance(el, dict) and "value" in el:
                return el.get("value") or ""
        return ""

    raise InteractionParseError(f"unsupported action.type {atype!r}")


# ─── Slack API client (httpx-based, mockable in tests) ───────────────


SLACK_API_BASE = "https://slack.com/api"


class SlackAPIError(Exception):
    """Wraps a non-`ok` Slack API response."""


async def slack_post(
    client: httpx.AsyncClient,
    method: str,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Generic helper — POST to a Slack `method` with a bearer token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    resp = await client.post(
        f"{SLACK_API_BASE}/{method}",
        headers=headers,
        json=payload,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackAPIError(f"slack {method} returned not-ok: {data.get('error')!r}")
    return data


async def conversations_open(client: httpx.AsyncClient, token: str, user_id: str) -> str:
    """Open a DM channel with a user; return the channel id."""
    data = await slack_post(client, "conversations.open", token, {"users": user_id})
    return ((data.get("channel") or {}).get("id")) or ""


async def chat_post_message(
    client: httpx.AsyncClient,
    token: str,
    channel: str,
    blocks: list[dict[str, Any]],
    text_fallback: str = "Atlas survey",
) -> str:
    """Post a Block Kit message to a channel; return the `ts`."""
    data = await slack_post(
        client,
        "chat.postMessage",
        token,
        {"channel": channel, "blocks": blocks, "text": text_fallback},
    )
    return data.get("ts", "")


async def users_lookup_by_email(client: httpx.AsyncClient, token: str, email: str) -> str | None:
    """Look up a Slack user id by email; return None if not in workspace."""
    try:
        data = await slack_post(client, "users.lookupByEmail", token, {"email": email})
    except SlackAPIError as exc:
        # users_not_found is the common case — return None rather than raise.
        if "users_not_found" in str(exc):
            return None
        raise
    return ((data.get("user") or {}).get("id")) or None


async def users_info_email(client: httpx.AsyncClient, token: str, user_id: str) -> str | None:
    """Look up a Slack user's profile email by slack user id.

    Returns None when Slack hides the email (user without
    `users:read.email` scope on a free workspace, or a guest user
    whose profile email isn't visible to the bot).

    Used by the inline-reminder-action handler to identify the
    *actor* (who clicked the button) for authorization — we never
    trust the user_id embedded in the action_id alone.
    """
    # users.info uses GET-style query semantics but Slack accepts the
    # same form-encoded JSON body our slack_post helper sends.
    try:
        data = await slack_post(client, "users.info", token, {"user": user_id})
    except SlackAPIError as exc:
        if "user_not_found" in str(exc):
            return None
        raise
    profile = (data.get("user") or {}).get("profile") or {}
    email = profile.get("email")
    return email or None


async def chat_delete(
    client: httpx.AsyncClient,
    token: str,
    channel: str,
    ts: str,
) -> None:
    """Delete a bot-authored message (GDPR right-to-erasure)."""
    await slack_post(client, "chat.delete", token, {"channel": channel, "ts": ts})


# ─── Form-encoded interaction payload helper ─────────────────────────


def parse_form_payload(form_body: bytes) -> dict[str, Any]:
    """Slack POSTs interactions as application/x-www-form-urlencoded with
    a single `payload=<urlencoded-JSON>` field. This helper handles
    the unwrap so the router can stay declarative.
    """
    from urllib.parse import parse_qs

    parsed = parse_qs(form_body.decode("utf-8"))
    raw = (parsed.get("payload") or [""])[0]
    if not raw:
        raise InteractionParseError("form body missing 'payload' field")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InteractionParseError(f"payload field is not valid JSON: {exc.msg}") from exc
