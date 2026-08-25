"""Survey-flow evaluator — pure functions, no DB.

Given a template (the JSON-parsed question list) and a partial
`answers` dict, decide:

  - which question to ask next, or
  - whether the survey is complete, or
  - whether the submitted answer is valid.

This module is dependency-free (no SQLAlchemy, no FastAPI, no
Pydantic) so it's trivial to unit-test and trivial to call from the
PR C Slack-interaction handler without dragging the rest of the
backend into that hot path.

Question schema (one entry per question):

    {
      "id": "uses-ai",
      "prompt": "Have you used an AI tool…?",
      "type": "yesNo" | "singleSelect" | "multiSelect" | "freeText",
      "options": ["Yes", "No", "Not sure"],   # for yesNo / *Select
      "optional": false,                       # default false
      "condition": {                           # show only if…
        "question": "uses-ai",
        "equals": "Yes"                        # exact match
        # — or —
        "includes": "Customer / grantee PII"   # for multiSelect prior
      }
    }

Adaptive flow: if Q1=No, every subsequent question's condition
`{"question": "uses-ai", "equals": "Yes"}` fails, so the evaluator
reports complete after Q1.

Answers shape:

    {
      "uses-ai": "Yes",
      "tools": ["Microsoft Copilot", "ChatGPT"],
      ...
    }

All public functions raise `SurveyValidationError` on malformed
input. Routers translate to HTTP 400.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

QuestionType = Literal["yesNo", "singleSelect", "multiSelect", "freeText"]
_ALLOWED_TYPES: set[str] = {"yesNo", "singleSelect", "multiSelect", "freeText"}


class SurveyValidationError(ValueError):
    """Raised when a template or an answer is malformed.

    Routers translate this to HTTP 400 with the message attached.
    """


# ─── Template loading ────────────────────────────────────────────────


@dataclass(frozen=True)
class Question:
    id: str
    prompt: str
    type: QuestionType
    options: tuple[str, ...]
    optional: bool
    condition: dict[str, Any] | None  # raw — see eval_condition

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Question:
        if not isinstance(raw, dict):
            raise SurveyValidationError(f"question must be an object, got {type(raw).__name__}")

        qid = raw.get("id")
        if not isinstance(qid, str) or not qid.strip():
            raise SurveyValidationError("question.id is required (non-empty string)")

        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SurveyValidationError(f"question.prompt is required for '{qid}'")

        qtype = raw.get("type")
        if qtype not in _ALLOWED_TYPES:
            raise SurveyValidationError(
                f"question.type for '{qid}' must be one of {sorted(_ALLOWED_TYPES)}, got {qtype!r}"
            )

        options_raw = raw.get("options")
        if qtype in {"yesNo", "singleSelect", "multiSelect"}:
            if not isinstance(options_raw, list) or len(options_raw) == 0:
                raise SurveyValidationError(
                    f"question '{qid}' (type {qtype}) requires non-empty options"
                )
            if not all(isinstance(o, str) and o.strip() for o in options_raw):
                raise SurveyValidationError(f"question '{qid}' options must be non-empty strings")
            options: tuple[str, ...] = tuple(options_raw)
        else:
            options = ()

        optional = bool(raw.get("optional", False))

        condition = raw.get("condition")
        if condition is not None and not isinstance(condition, dict):
            raise SurveyValidationError(f"question '{qid}' condition must be an object or null")

        return cls(
            id=qid.strip(),
            prompt=prompt.strip(),
            type=qtype,  # type: ignore[arg-type]
            options=options,
            optional=optional,
            condition=condition,
        )


def parse_questions(raw_json: str | list[dict[str, Any]]) -> list[Question]:
    """Parse a template's questions JSON into typed Question dataclasses.

    Accepts either a JSON string (the DB column) or an already-decoded
    list (the test fixtures + the bot DM handler). Raises
    `SurveyValidationError` on malformed input.

    Duplicate question ids are an error — the next-question logic
    relies on `id` being unique.
    """
    if isinstance(raw_json, str):
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise SurveyValidationError(
                f"template questions_json is not valid JSON: {exc.msg}"
            ) from exc
    else:
        decoded = raw_json

    if not isinstance(decoded, list):
        raise SurveyValidationError(
            f"template questions must be a list, got {type(decoded).__name__}"
        )
    if len(decoded) == 0:
        raise SurveyValidationError("template must have at least one question")

    parsed: list[Question] = []
    seen: set[str] = set()
    for entry in decoded:
        q = Question.from_dict(entry)
        if q.id in seen:
            raise SurveyValidationError(f"duplicate question id '{q.id}' in template")
        seen.add(q.id)
        parsed.append(q)
    return parsed


# ─── Condition evaluation ────────────────────────────────────────────


def eval_condition(condition: dict[str, Any] | None, answers: dict[str, Any]) -> bool:
    """Return True if `condition` is satisfied by `answers`.

    Condition shapes:

      None                                 → always true
      {"question": "id", "equals": "Yes"}  → answers["id"] == "Yes"
      {"question": "id", "includes": "x"}  → "x" in answers["id"]
                                              (answer must be a list)

    Unknown / malformed conditions raise `SurveyValidationError` so
    template bugs surface loud.
    """
    if condition is None:
        return True

    qid = condition.get("question")
    if not isinstance(qid, str) or not qid.strip():
        raise SurveyValidationError(f"condition.question is required (got {qid!r})")

    answered = answers.get(qid, _UNSET)

    if "equals" in condition:
        expected = condition["equals"]
        if answered is _UNSET:
            return False
        return answered == expected

    if "includes" in condition:
        item = condition["includes"]
        if answered is _UNSET or not isinstance(answered, list):
            return False
        return item in answered

    raise SurveyValidationError(f"condition for '{qid}' missing 'equals' or 'includes'")


# Sentinel for "key absent" vs "key present with value None".
_UNSET: object = object()


# ─── Next-question evaluation ────────────────────────────────────────


@dataclass(frozen=True)
class NextStep:
    """Result of `next_question`. Either a question to ask or done."""

    question: Question | None
    is_complete: bool

    @classmethod
    def complete(cls) -> NextStep:
        return cls(question=None, is_complete=True)

    @classmethod
    def ask(cls, question: Question) -> NextStep:
        return cls(question=question, is_complete=False)


def next_question(questions: list[Question], answers: dict[str, Any]) -> NextStep:
    """Return the next question to ask, or `NextStep.complete()`.

    Walks the questions in declared order. For each:

      - Evaluate its condition against `answers`. If false, skip
        (it's not applicable to this respondent's path).
      - If the id is already in `answers`, skip.
      - Otherwise return it.

    Returns `complete` if no question remains.
    """
    for q in questions:
        if not eval_condition(q.condition, answers):
            continue
        if q.id in answers:
            continue
        return NextStep.ask(q)
    return NextStep.complete()


# ─── Answer validation ───────────────────────────────────────────────


def validate_answer(question: Question, raw_value: Any) -> Any:
    """Validate and canonicalise an answer for the given question.

    Returns the value to store in `answers_json`. Raises
    `SurveyValidationError` if the value doesn't fit the question
    type.

    Canonicalisation:
      - yesNo / singleSelect → stripped string, must match an option
      - multiSelect          → list of stripped strings, all options,
                                deduped, original order preserved
      - freeText             → stripped string; allowed empty/None if
                                question is optional
    """
    if question.type == "yesNo" or question.type == "singleSelect":
        if not isinstance(raw_value, str):
            raise SurveyValidationError(
                f"question '{question.id}' expects a string, got {type(raw_value).__name__}"
            )
        value = raw_value.strip()
        if not value:
            if question.optional:
                # Skip rather than store empty — optional questions
                # can be omitted entirely.
                raise SurveyValidationError(
                    f"question '{question.id}' is optional but received "
                    "empty value; omit the answer instead"
                )
            raise SurveyValidationError(f"question '{question.id}' requires a non-empty answer")
        if value not in question.options:
            raise SurveyValidationError(
                f"answer for '{question.id}' ({value!r}) is not in options {list(question.options)}"
            )
        return value

    if question.type == "multiSelect":
        if not isinstance(raw_value, list):
            raise SurveyValidationError(
                f"question '{question.id}' expects a list, got {type(raw_value).__name__}"
            )
        canonical: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            if not isinstance(item, str):
                raise SurveyValidationError(f"question '{question.id}' list items must be strings")
            stripped = item.strip()
            if not stripped:
                continue
            if stripped not in question.options:
                raise SurveyValidationError(
                    f"item {stripped!r} for '{question.id}' is not in options "
                    f"{list(question.options)}"
                )
            if stripped not in seen:
                seen.add(stripped)
                canonical.append(stripped)
        if not canonical and not question.optional:
            raise SurveyValidationError(f"question '{question.id}' requires at least one selection")
        return canonical

    if question.type == "freeText":
        if raw_value is None:
            if not question.optional:
                raise SurveyValidationError(f"question '{question.id}' is required")
            return None
        if not isinstance(raw_value, str):
            raise SurveyValidationError(
                f"question '{question.id}' expects a string, got {type(raw_value).__name__}"
            )
        value = raw_value.strip()
        if not value:
            if not question.optional:
                raise SurveyValidationError(f"question '{question.id}' requires a non-empty answer")
            return None
        return value

    raise SurveyValidationError(f"unknown question type {question.type!r}")


# ─── Foundation default template ─────────────────────────────────────
#
# This is the canonical 5-question foundation template described in
# `docs/use-case-survey-bot.md` §3. Mirrors the frontend's
# `USE_CASE_SURVEY_TEMPLATE` constant in `curated-demo-data.ts` —
# same slug, same version, same question shape, so the demo path
# works end-to-end before any custom templates exist.
#
# The seed function (`seed_foundation_template`) inserts this into
# the SurveyTemplate table for a given tenant, idempotent on
# (tenant_id, slug, version). Called on tenant bootstrap (PR future)
# and from test fixtures.

FOUNDATION_TEMPLATE_SLUG = "foundation-use-case"
FOUNDATION_TEMPLATE_VERSION = "v1"
FOUNDATION_TEMPLATE_NAME = "Foundation AI use case (default)"
FOUNDATION_TEMPLATE_DESCRIPTION = (
    "5-question flow for foundation staff to register an AI use case. "
    "~60–90s to complete via Slack DM."
)

FOUNDATION_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "uses-ai",
        "prompt": "Have you used an AI tool for work in the last 7 days?",
        "type": "singleSelect",
        "options": ["Yes", "No", "Not sure"],
    },
    {
        "id": "tools",
        "prompt": "Which tool(s)? Pick all that apply.",
        "type": "multiSelect",
        "options": [
            "Microsoft Copilot",
            "ChatGPT",
            "Claude",
            "Gemini",
            "Perplexity",
            "Other",
        ],
        "condition": {"question": "uses-ai", "equals": "Yes"},
    },
    {
        "id": "tasks",
        "prompt": "What for? Pick all that apply.",
        "type": "multiSelect",
        "options": [
            "Drafting documents",
            "Summarising",
            "Research",
            "Coding",
            "Data analysis",
            "Brainstorming",
            "Other",
        ],
        "condition": {"question": "uses-ai", "equals": "Yes"},
    },
    {
        "id": "data-classes",
        "prompt": "Have you pasted any of the following into an AI tool?",
        "type": "multiSelect",
        "options": [
            "Customer / grantee PII",
            "Employee PII",
            "Financial data",
            "Proprietary code",
            "Strategy docs",
            "None of the above",
        ],
        "condition": {"question": "uses-ai", "equals": "Yes"},
    },
    {
        "id": "notes",
        "prompt": ("Anything else IT should know? (Optional — skip if not applicable.)"),
        "type": "freeText",
        "optional": True,
        "condition": {"question": "uses-ai", "equals": "Yes"},
    },
]


def foundation_template_json() -> str:
    """Return the foundation template questions as a JSON string.

    For inserting into `SurveyTemplate.questions_json`.
    """
    return json.dumps(FOUNDATION_QUESTIONS)
