"""Unit tests for app.services.survey_flow.

Pure-function tests — no DB, no FastAPI. Covers:

  - parse_questions: shape validation, duplicate-id detection
  - eval_condition: equals / includes / unset / unknown shape
  - next_question: ordered walk, condition gating, completion
  - validate_answer: type coercion + canonicalisation + invalid-option
  - foundation template loads cleanly + adaptive flow behaves
"""

from __future__ import annotations

import json

import pytest

from app.services.survey_flow import (
    FOUNDATION_QUESTIONS,
    Question,
    SurveyValidationError,
    eval_condition,
    foundation_template_json,
    next_question,
    parse_questions,
    validate_answer,
)

pytestmark = [pytest.mark.unit]


# ─── parse_questions ─────────────────────────────────────────────────


class TestParseQuestions:
    def test_foundation_template_parses(self) -> None:
        questions = parse_questions(FOUNDATION_QUESTIONS)
        assert len(questions) == 5
        assert [q.id for q in questions] == [
            "uses-ai",
            "tools",
            "tasks",
            "data-classes",
            "notes",
        ]

    def test_parses_json_string(self) -> None:
        as_json = foundation_template_json()
        questions = parse_questions(as_json)
        assert len(questions) == 5

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(SurveyValidationError):
            parse_questions("not json")

    def test_non_list_raises(self) -> None:
        with pytest.raises(SurveyValidationError):
            parse_questions('{"id": "x"}')  # JSON object, not array

    def test_empty_list_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="at least one"):
            parse_questions("[]")

    def test_missing_id_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="id"):
            parse_questions([{"prompt": "no id?", "type": "yesNo", "options": ["a"]}])

    def test_missing_prompt_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="prompt"):
            parse_questions([{"id": "q1", "type": "yesNo", "options": ["a"]}])

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="type"):
            parse_questions([{"id": "q1", "prompt": "p", "type": "weirdtype"}])

    def test_select_requires_options(self) -> None:
        with pytest.raises(SurveyValidationError, match="options"):
            parse_questions([{"id": "q1", "prompt": "p", "type": "yesNo", "options": []}])

    def test_duplicate_id_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="duplicate"):
            parse_questions(
                [
                    {"id": "q1", "prompt": "p", "type": "freeText"},
                    {"id": "q1", "prompt": "p2", "type": "freeText"},
                ]
            )

    def test_freetext_optional(self) -> None:
        questions = parse_questions(
            [
                {
                    "id": "q1",
                    "prompt": "Anything?",
                    "type": "freeText",
                    "optional": True,
                },
            ]
        )
        assert questions[0].optional is True

    def test_condition_must_be_dict_or_none(self) -> None:
        with pytest.raises(SurveyValidationError):
            parse_questions(
                [
                    {
                        "id": "q1",
                        "prompt": "p",
                        "type": "freeText",
                        "condition": "not a dict",
                    }
                ]
            )


# ─── eval_condition ──────────────────────────────────────────────────


class TestEvalCondition:
    def test_none_condition_always_true(self) -> None:
        assert eval_condition(None, {}) is True
        assert eval_condition(None, {"q": "anything"}) is True

    def test_equals_match(self) -> None:
        assert eval_condition({"question": "q", "equals": "Yes"}, {"q": "Yes"}) is True

    def test_equals_mismatch(self) -> None:
        assert eval_condition({"question": "q", "equals": "Yes"}, {"q": "No"}) is False

    def test_equals_unanswered_is_false(self) -> None:
        assert eval_condition({"question": "q", "equals": "Yes"}, {}) is False

    def test_includes_in_list(self) -> None:
        assert (
            eval_condition(
                {"question": "q", "includes": "X"},
                {"q": ["X", "Y"]},
            )
            is True
        )

    def test_includes_not_in_list(self) -> None:
        assert (
            eval_condition(
                {"question": "q", "includes": "Z"},
                {"q": ["X", "Y"]},
            )
            is False
        )

    def test_includes_against_non_list_is_false(self) -> None:
        assert (
            eval_condition(
                {"question": "q", "includes": "X"},
                {"q": "X"},
            )
            is False
        )

    def test_missing_question_field_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match="question"):
            eval_condition({"equals": "Yes"}, {})

    def test_missing_operator_raises(self) -> None:
        with pytest.raises(SurveyValidationError, match=r"equals.*includes"):
            eval_condition({"question": "q"}, {})


# ─── next_question (the heart of the engine) ─────────────────────────


class TestNextQuestion:
    def setup_method(self) -> None:
        self.foundation = parse_questions(FOUNDATION_QUESTIONS)

    def test_empty_answers_returns_q1(self) -> None:
        step = next_question(self.foundation, {})
        assert step.is_complete is False
        assert step.question is not None
        assert step.question.id == "uses-ai"

    def test_answered_q1_yes_returns_q2(self) -> None:
        step = next_question(self.foundation, {"uses-ai": "Yes"})
        assert step.is_complete is False
        assert step.question is not None
        assert step.question.id == "tools"

    def test_answered_q1_no_completes_immediately(self) -> None:
        """The adaptive flow: every other Q gates on uses-ai=Yes."""
        step = next_question(self.foundation, {"uses-ai": "No"})
        assert step.is_complete is True
        assert step.question is None

    def test_answered_q1_not_sure_completes(self) -> None:
        step = next_question(self.foundation, {"uses-ai": "Not sure"})
        assert step.is_complete is True

    def test_full_yes_path_walks_through(self) -> None:
        """Walk Q1=Yes through to completion."""
        answers: dict = {}
        ordered: list[str] = []
        # Cap iterations as a safety belt against an infinite loop bug.
        for _ in range(10):
            step = next_question(self.foundation, answers)
            if step.is_complete:
                break
            assert step.question is not None
            ordered.append(step.question.id)
            # Provide a canned answer for each type.
            if step.question.type == "singleSelect":
                answers[step.question.id] = step.question.options[0]
            elif step.question.type == "multiSelect":
                answers[step.question.id] = [step.question.options[0]]
            elif step.question.type == "freeText":
                answers[step.question.id] = "demo note"
        assert ordered == ["uses-ai", "tools", "tasks", "data-classes", "notes"]

    def test_skip_optional_freetext_still_completes(self) -> None:
        # Optional questions still get a key in `answers` once
        # explicitly handled. Simulate that the bot stored None for
        # 'notes' to mark it skipped.
        answers = {
            "uses-ai": "Yes",
            "tools": ["Microsoft Copilot"],
            "tasks": ["Drafting documents"],
            "data-classes": ["None of the above"],
            "notes": None,
        }
        step = next_question(self.foundation, answers)
        assert step.is_complete is True

    def test_partial_answers_returns_next_open(self) -> None:
        answers = {
            "uses-ai": "Yes",
            "tools": ["ChatGPT"],
            # tasks missing
        }
        step = next_question(self.foundation, answers)
        assert step.question is not None
        assert step.question.id == "tasks"


# ─── validate_answer ─────────────────────────────────────────────────


class TestValidateAnswer:
    def setup_method(self) -> None:
        self.foundation = parse_questions(FOUNDATION_QUESTIONS)
        self.by_id = {q.id: q for q in self.foundation}

    def test_yesno_valid(self) -> None:
        q = self.by_id["uses-ai"]
        assert validate_answer(q, "Yes") == "Yes"

    def test_yesno_trims(self) -> None:
        q = self.by_id["uses-ai"]
        assert validate_answer(q, "  Yes  ") == "Yes"

    def test_yesno_not_in_options(self) -> None:
        q = self.by_id["uses-ai"]
        with pytest.raises(SurveyValidationError, match="not in options"):
            validate_answer(q, "Maybe")

    def test_yesno_wrong_type(self) -> None:
        q = self.by_id["uses-ai"]
        with pytest.raises(SurveyValidationError, match="expects a string"):
            validate_answer(q, ["Yes"])

    def test_multiselect_valid(self) -> None:
        q = self.by_id["tools"]
        result = validate_answer(q, ["ChatGPT", "Microsoft Copilot"])
        assert result == ["ChatGPT", "Microsoft Copilot"]

    def test_multiselect_dedupes_preserving_order(self) -> None:
        q = self.by_id["tools"]
        result = validate_answer(q, ["ChatGPT", "ChatGPT", "Claude", "ChatGPT"])
        assert result == ["ChatGPT", "Claude"]

    def test_multiselect_trims_and_filters_empties(self) -> None:
        q = self.by_id["tools"]
        result = validate_answer(q, ["  ChatGPT  ", "", "  "])
        assert result == ["ChatGPT"]

    def test_multiselect_rejects_unknown_option(self) -> None:
        q = self.by_id["tools"]
        with pytest.raises(SurveyValidationError, match="not in options"):
            validate_answer(q, ["ChatGPT", "TotallyMadeUp"])

    def test_multiselect_requires_at_least_one_when_not_optional(self) -> None:
        q = self.by_id["tools"]
        # tools is not optional
        with pytest.raises(SurveyValidationError, match="at least one"):
            validate_answer(q, [])

    def test_multiselect_wrong_type(self) -> None:
        q = self.by_id["tools"]
        with pytest.raises(SurveyValidationError, match="expects a list"):
            validate_answer(q, "ChatGPT")

    def test_freetext_optional_none(self) -> None:
        q = self.by_id["notes"]  # optional freeText
        assert validate_answer(q, None) is None

    def test_freetext_optional_empty_string_becomes_none(self) -> None:
        q = self.by_id["notes"]
        assert validate_answer(q, "   ") is None

    def test_freetext_optional_non_empty_kept(self) -> None:
        q = self.by_id["notes"]
        assert validate_answer(q, "  recurring weekly  ") == "recurring weekly"

    def test_freetext_required(self) -> None:
        q = Question.from_dict({"id": "req", "prompt": "p", "type": "freeText"})
        with pytest.raises(SurveyValidationError, match="required"):
            validate_answer(q, None)


# ─── Foundation full-flow smoke ──────────────────────────────────────


class TestFoundationFullFlow:
    """End-to-end happy + skip paths against the canonical template."""

    def test_yes_full_path_completes(self) -> None:
        questions = parse_questions(FOUNDATION_QUESTIONS)
        answers: dict = {}

        # Q1
        step = next_question(questions, answers)
        assert step.question is not None
        assert step.question.id == "uses-ai"
        answers["uses-ai"] = validate_answer(step.question, "Yes")

        # Q2
        step = next_question(questions, answers)
        assert step.question is not None
        assert step.question.id == "tools"
        answers["tools"] = validate_answer(step.question, ["Microsoft Copilot", "ChatGPT"])

        # Q3
        step = next_question(questions, answers)
        assert step.question is not None and step.question.id == "tasks"
        answers["tasks"] = validate_answer(step.question, ["Drafting documents", "Summarising"])

        # Q4
        step = next_question(questions, answers)
        assert step.question is not None and step.question.id == "data-classes"
        answers["data-classes"] = validate_answer(
            step.question, ["Customer / grantee PII", "Financial data"]
        )

        # Q5 (optional)
        step = next_question(questions, answers)
        assert step.question is not None and step.question.id == "notes"
        # Skip → store None
        answers["notes"] = validate_answer(step.question, None)

        # Now complete
        step = next_question(questions, answers)
        assert step.is_complete is True

    def test_no_path_short_circuits(self) -> None:
        questions = parse_questions(FOUNDATION_QUESTIONS)
        answers = {"uses-ai": "No"}
        step = next_question(questions, answers)
        assert step.is_complete is True

    def test_foundation_json_roundtrip(self) -> None:
        as_json = foundation_template_json()
        decoded = json.loads(as_json)
        assert decoded == FOUNDATION_QUESTIONS
