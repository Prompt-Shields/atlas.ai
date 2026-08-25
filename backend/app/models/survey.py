"""Survey engine — templates, deliveries, responses, opt-outs.

PR B of the survey-bot backend stack. Owns the abstract question-and-
answer machinery; the Slack-specific delivery transport lands in PR C.

Domain shape:

  ┌──────────────────┐   1   N   ┌──────────────────┐
  │  SurveyTemplate  │ ────────► │ SurveyDelivery   │
  │  (questions JSON)│           │ (audience + sent)│
  └──────────────────┘           └────────┬─────────┘
                                          │ 1
                                          │
                                          ▼ N
                                ┌────────────────────┐
                                │  SurveyResponse    │
                                │  (per-recipient)   │
                                └────────────────────┘

`SlackOptOut` is a tenant-scoped list of Slack user IDs that have
replied STOP to a bot DM. The PR C dispatch worker checks this list
before sending; we keep the model here in PR B so the GDPR erasure
endpoint (lives in PR B) can also write to it.

All five tables are tenant-scoped. The questions live as a JSON
string in `SurveyTemplate.questions_json` — same Text-as-JSON
convention as `UseCase.data_classes`. The flow evaluator
(`app.services.survey_flow`) parses on every advance, so we don't
risk drift between the row content and the evaluator's view of it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin

# ─── Enums ────────────────────────────────────────────────────────────


class DeliveryChannel(str, enum.Enum):
    SLACK = "SLACK"
    EMAIL = "EMAIL"  # Email fallback is v0.2; reserved here for forward-compat.


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"  # Created, recipients resolved, worker not yet picked up
    SENDING = "SENDING"  # Worker actively dispatching DMs
    SENT = "SENT"  # All recipient DMs dispatched (or marked failed)
    FAILED = "FAILED"  # Hard failure (e.g. Slack token revoked)
    CANCELED = "CANCELED"  # Admin canceled before send


class ResponseStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    OPTED_OUT = "OPTED_OUT"  # User replied STOP mid-flow
    EXPIRED = "EXPIRED"  # Worker gave up after the survey TTL


# ─── SurveyTemplate ───────────────────────────────────────────────────


class SurveyTemplate(GRCBase, TenantScopedMixin, TestDataMixin):
    """A question flow definition.

    Templates are versioned. The foundation default (id stable across
    tenants: `foundation-use-case-v1`) is seeded on tenant bootstrap;
    customers can author additional templates via /api/v1/surveys/
    templates.

    `slug` is the human/machine-readable identifier shared across
    versions (e.g. `foundation-use-case`); pair `(tenant_id, slug,
    version)` is unique. The frontend `templateId` field maps to
    `slug` + `version` resolution server-side.
    """

    __tablename__ = "survey_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "slug",
            "version",
            name="uq_grc_survey_templates_tenant_slug_version",
        ),
        {"schema": "grc"},
    )

    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON array of question definitions. Schema validated by
    # `app.services.survey_flow.load_template`. Stored as Text for
    # portability across SQLite test fixtures and Postgres prod.
    questions_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JSON array of question definitions; see survey_flow.parse_questions",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ─── SurveyDelivery ───────────────────────────────────────────────────


class SurveyDelivery(GRCBase, TenantScopedMixin, TestDataMixin):
    """A dispatch instance — one template sent to one audience.

    Created when the admin clicks "Send survey" in the /dashboard/
    registry modal. `recipient_count` is set at creation time;
    `completed_count` is updated as responses come in (cheap counter,
    not a join — the join exists for audits).
    """

    __tablename__ = "survey_deliveries"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.survey_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Human label e.g. "Foundation Q2 registration". Defaults to the
    # template name + date if the admin doesn't customise.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Audience filter shape:
    #   {"mode": "all"}
    #   {"mode": "departments", "values": [...]}
    #   {"mode": "tools", "values": [...]}
    #   {"mode": "custom", "values": ["alice@x.com", ...]}
    audience_filter_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JSON {mode, values?} — see survey_audience.resolve",
    )
    # Friendly audience label cached for the recent-surveys card so
    # we don't recompute on every list call.
    audience_label: Mapped[str] = mapped_column(String(255), nullable=False)

    channel: Mapped[DeliveryChannel] = mapped_column(
        Enum(DeliveryChannel, schema="grc"),
        nullable=False,
        default=DeliveryChannel.SLACK,
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, schema="grc"),
        nullable=False,
        default=DeliveryStatus.PENDING,
        index=True,
    )

    sent_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_registration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps distinct from the GRCBase created_at — created_at is
    # when the row was inserted; dispatched_at is when the worker
    # actually started sending the first DM.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ─── SurveyResponse ───────────────────────────────────────────────────


class SurveyResponse(GRCBase, TenantScopedMixin, TestDataMixin):
    """One survey delivered to one recipient.

    Created at dispatch time (one row per recipient, even before they
    open the DM). Status walks NOT_STARTED → IN_PROGRESS → COMPLETED.
    `answers_json` accumulates as questions are answered.

    `slack_user_id` is the recipient's Slack user id when the channel
    is SLACK. `user_id` (atlas user) is set if we resolved them to a
    local user — null for external recipients (e.g. custom email list
    when EMAIL channel arrives in v0.2).

    GDPR right-to-erasure: DELETE /api/v1/surveys/responses/{id}
    wipes this row + the corresponding Slack message via chat.delete
    (PR C). The `registered_as_use_case_id` link is broken via
    SET NULL on the use_cases.dispatched_from_response_id column to
    preserve the catalogue record.
    """

    __tablename__ = "survey_responses"

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.survey_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The recipient identity. At least one of user_id / slack_user_id
    # is populated.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slack_user_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
        comment="Slack user id (Uxxxxx). Null for non-Slack recipients.",
    )
    recipient_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        comment="Cached at dispatch time for audit; lower-cased.",
    )

    status: Mapped[ResponseStatus] = mapped_column(
        Enum(ResponseStatus, schema="grc"),
        nullable=False,
        default=ResponseStatus.NOT_STARTED,
        index=True,
    )

    # Answers accumulate as the user advances through the flow.
    # Shape:
    #   {"question_id": <answer>}
    # where <answer> is a string, list[str], or None depending on the
    # question type. Empty {} at creation.
    answers_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default="{}",
        comment="JSON map of question_id → answer (string | list[str] | null)",
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provenance — the use_case row created when this response was
    # promoted. Forward-typed UUID (no FK on use_cases.id from this
    # column to avoid the circular: use_cases also has dispatched
    # _from_response_id pointing at us).
    registered_as_use_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # Slack-specific delivery tracking — populated by the PR C worker.
    slack_channel_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="ts of the most recent question DM — used by chat.delete on erasure",
    )


# ─── SlackOptOut ──────────────────────────────────────────────────────


class SlackOptOut(GRCBase, TenantScopedMixin, TestDataMixin):
    """A Slack user id that has opted out of bot surveys.

    Written when the bot DM receives a `STOP` reply (PR C webhook).
    The dispatch worker filters this list out of every audience
    before sending. Per-tenant: opting out in Tenant A doesn't
    suppress surveys from Tenant B (different bot, different
    workspace).
    """

    __tablename__ = "slack_opt_outs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slack_user_id", name="uq_grc_slack_opt_outs_tenant_user"),
        {"schema": "grc"},
    )

    slack_user_id: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Free-form, defaults to 'STOP reply'",
    )
