"""Reconcile the migrated schema with the ORM models.

`alembic upgrade head` produced a schema the application could not actually
use. Commit 6312818 (2026-05-15) renamed three pipeline tables in the models
and rewrote `001_initial_schema` in place, but left `001` creating the *old*
names — so a database built from migrations had `source_of_truth_blobs`,
`risk_analyses` and `governance_correlations` while every query in
`app/` addressed `blob_records`, `risk_mitigations` and
`correlation_action_plans`. `user_activity_blobs` was dropped from the models
entirely.

Alongside that: 27 indexes the models declare were never created, 70 carry
names from before the ORM's `ix_<schema>_<table>_<column>` convention,
`tenant_settings.research_all_data` was missing, and 61 column comments never
made it into the database. Together those were the whole of what
`alembic check` reported.

**Written defensively on purpose.** Environments are in inconsistent states:
the migration chain was unrunnable for months (duplicate revision ids, fixed in
040), and at least one development database carries the *new* table names while
stamped at an older revision. Every statement here is therefore guarded — it
renames only when the old name is present and the new one is not, creates only
what is missing, and is safe to re-run. That is deliberately not idiomatic
alembic; a plain `ALTER TABLE ... RENAME` would fail outright on roughly half
the databases this has to run against.

Column deltas applied with the renames:

  blob_records          source_category -> source_type, reported_by -> created_by,
                        + source_id, + adapter_name, title becomes nullable
  risk_mitigations      source_of_truth_blob_id -> blob_id
  correlation_action_plans
                        risk_analysis_ids -> risk_mitigation_ids, drops
                        user_activity_blob_ids, score_reliability, match_tags,
                        is_excluded, excluded_by, excluded_at, exclusion_reason

Revision ID: 041
Revises: 040
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── helpers ────────────────────────────────────────────────────────────

_RENAME_TABLE = """
DO $$
BEGIN
  IF to_regclass('grc.{old}') IS NOT NULL AND to_regclass('grc.{new}') IS NULL THEN
    EXECUTE 'ALTER TABLE grc.{old} RENAME TO {new}';
  END IF;
END $$;
"""

_RENAME_COLUMN = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'grc' AND table_name = '{table}'
               AND column_name = '{old}')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'grc' AND table_name = '{table}'
               AND column_name = '{new}') THEN
    EXECUTE 'ALTER TABLE grc.{table} RENAME COLUMN {old} TO {new}';
  END IF;
END $$;
"""

_RENAME_CONSTRAINT = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{old}')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{new}') THEN
    EXECUTE 'ALTER TABLE grc.{table} RENAME CONSTRAINT {old} TO {new}';
  END IF;
END $$;
"""


def _rename_table(old: str, new: str) -> None:
    op.execute(_RENAME_TABLE.format(old=old, new=new))


def _rename_column(table: str, old: str, new: str) -> None:
    op.execute(_RENAME_COLUMN.format(table=table, old=old, new=new))


def _rename_constraint(table: str, old: str, new: str) -> None:
    op.execute(_RENAME_CONSTRAINT.format(table=table, old=old, new=new))


# ── upgrade ────────────────────────────────────────────────────────────


def _reconcile_pipeline_tables() -> None:
    # user_activity_blobs has no model and nothing reads it.
    op.execute("DROP TABLE IF EXISTS grc.user_activity_blobs CASCADE")

    # source_of_truth_blobs -> blob_records
    _rename_table("source_of_truth_blobs", "blob_records")
    _rename_column("blob_records", "source_category", "source_type")
    _rename_column("blob_records", "reported_by", "created_by")
    op.execute("ALTER TABLE grc.blob_records ADD COLUMN IF NOT EXISTS source_id VARCHAR(255)")
    op.execute(
        "ALTER TABLE grc.blob_records"
        " ADD COLUMN IF NOT EXISTS adapter_name VARCHAR(100) NOT NULL DEFAULT ''"
    )
    # The model has no server default; it was only needed to backfill.
    op.execute("ALTER TABLE grc.blob_records ALTER COLUMN adapter_name DROP DEFAULT")
    op.execute("ALTER TABLE grc.blob_records ALTER COLUMN source_type TYPE VARCHAR(50)")
    op.execute("ALTER TABLE grc.blob_records ALTER COLUMN title DROP NOT NULL")

    # risk_analyses -> risk_mitigations
    _rename_table("risk_analyses", "risk_mitigations")
    _rename_column("risk_mitigations", "source_of_truth_blob_id", "blob_id")

    # governance_correlations -> correlation_action_plans
    _rename_table("governance_correlations", "correlation_action_plans")
    _rename_column("correlation_action_plans", "risk_analysis_ids", "risk_mitigation_ids")
    for col in (
        "user_activity_blob_ids",
        "score_reliability",
        "match_tags",
        "is_excluded",
        "excluded_by",
        "excluded_at",
        "exclusion_reason",
    ):
        op.execute(f"ALTER TABLE grc.correlation_action_plans DROP COLUMN IF EXISTS {col}")

    # The FK on dispatch_events follows the renamed table automatically; its
    # constraint name does not.
    _rename_constraint(
        "dispatch_events",
        "fk_dispatch_events_correlation_id_governance_correlations",
        "fk_dispatch_events_correlation_id_correlation_action_plans",
    )


def _rename_legacy_indexes() -> None:
    """Add the schema prefix the ORM's default index naming produces.

    Same table, same columns, same uniqueness — only the name differs, so this
    is `ALTER INDEX ... RENAME` rather than a drop/create.
    """
    op.execute(
        """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
            ('audit', 'ix_audit_logs_actor_id', 'ix_audit_audit_logs_actor_id'),
            ('audit', 'ix_audit_logs_event_type', 'ix_audit_audit_logs_event_type'),
            ('audit', 'ix_audit_logs_tenant_id', 'ix_audit_audit_logs_tenant_id'),
            ('audit', 'ix_audit_logs_timestamp', 'ix_audit_audit_logs_timestamp'),
            ('grc', 'ix_api_keys_user_id', 'ix_grc_api_keys_user_id'),
            ('grc', 'ix_dev_events_api_key_id', 'ix_grc_developer_events_api_key_id'),
            ('grc', 'ix_dev_events_correlation_id', 'ix_grc_developer_events_correlation_id'),
            ('grc', 'ix_dev_events_event_type', 'ix_grc_developer_events_event_type'),
            ('grc', 'ix_dev_events_occurred_at', 'ix_grc_developer_events_occurred_at'),
            ('grc', 'ix_dev_events_session_id', 'ix_grc_developer_events_session_id'),
            ('grc', 'ix_dev_events_severity', 'ix_grc_developer_events_severity'),
            ('grc', 'ix_dev_events_source', 'ix_grc_developer_events_source'),
            ('grc', 'ix_dev_events_tenant_id', 'ix_grc_developer_events_tenant_id'),
            ('grc', 'ix_developer_api_key_scopes_api_key_id', 'ix_grc_developer_api_key_scopes_api_key_id'),
            ('grc', 'ix_dispatch_events_correlation_id', 'ix_grc_dispatch_events_correlation_id'),
            ('grc', 'ix_dispatch_events_org_id', 'ix_grc_dispatch_events_org_id'),
            ('grc', 'ix_dispatch_events_tenant_id', 'ix_grc_dispatch_events_tenant_id'),
            ('grc', 'ix_event_defs_name', 'ix_grc_developer_event_definitions_name'),
            ('grc', 'ix_event_defs_tenant_id', 'ix_grc_developer_event_definitions_tenant_id'),
            ('grc', 'ix_governance_correlations_batch_id', 'ix_grc_correlation_action_plans_batch_id'),
            ('grc', 'ix_governance_correlations_org_id', 'ix_grc_correlation_action_plans_org_id'),
            ('grc', 'ix_governance_correlations_tenant_id', 'ix_grc_correlation_action_plans_tenant_id'),
            ('grc', 'ix_grc_agent_control_states_tenant', 'ix_grc_agent_control_states_tenant_id'),
            ('grc', 'ix_grc_ai_cost_records_integration', 'ix_grc_ai_cost_records_integration_id'),
            ('grc', 'ix_grc_cad_app_domain', 'ix_grc_cloud_app_detections_app_domain'),
            ('grc', 'ix_grc_cad_app_name', 'ix_grc_cloud_app_detections_app_name'),
            ('grc', 'ix_grc_cad_external_detection_id', 'ix_grc_cloud_app_detections_external_detection_id'),
            ('grc', 'ix_grc_cad_integration_id', 'ix_grc_cloud_app_detections_integration_id'),
            ('grc', 'ix_grc_cad_last_seen_at', 'ix_grc_cloud_app_detections_last_seen_at'),
            ('grc', 'ix_grc_cad_tenant_id', 'ix_grc_cloud_app_detections_tenant_id'),
            ('grc', 'ix_grc_cad_user_upn', 'ix_grc_cloud_app_detections_user_principal_name'),
            ('grc', 'ix_grc_discovered_agents_tenant', 'ix_grc_discovered_agents_tenant_id'),
            ('grc', 'ix_grc_discovered_defender_apps_tenant', 'ix_grc_discovered_defender_apps_tenant_id'),
            ('grc', 'ix_grc_discovered_mcp_servers_tenant', 'ix_grc_discovered_mcp_servers_tenant_id'),
            ('grc', 'ix_grc_extension_heartbeats_fingerprint', 'ix_grc_extension_device_heartbeats_device_fingerprint'),
            ('grc', 'ix_grc_extension_heartbeats_last_seen', 'ix_grc_extension_device_heartbeats_last_seen_at'),
            ('grc', 'ix_grc_extension_heartbeats_user_email', 'ix_grc_extension_device_heartbeats_user_email'),
            ('grc', 'ix_grc_handbook_tenant_id', 'ix_grc_handbook_acknowledgements_tenant_id'),
            ('grc', 'ix_grc_handbook_user_id', 'ix_grc_handbook_acknowledgements_user_id'),
            ('grc', 'ix_grc_intune_devices_enrolled_upn', 'ix_grc_managed_devices_enrolled_user_email'),
            ('grc', 'ix_grc_intune_devices_external_device_id', 'ix_grc_managed_devices_external_device_id'),
            ('grc', 'ix_grc_intune_devices_integration_id', 'ix_grc_managed_devices_integration_id'),
            ('grc', 'ix_grc_intune_devices_tenant_id', 'ix_grc_managed_devices_tenant_id'),
            ('grc', 'ix_grc_purview_events_actor_upn', 'ix_grc_purview_events_actor_user_principal_name'),
            ('grc', 'ix_grc_saas_vendor_profiles_tenant', 'ix_grc_saas_vendor_profiles_tenant_id'),
            ('grc', 'ix_invites_email', 'ix_grc_invites_email'),
            ('grc', 'ix_llm_usage_records_job_id', 'ix_grc_llm_usage_records_job_id'),
            ('grc', 'ix_llm_usage_records_org_id', 'ix_grc_llm_usage_records_org_id'),
            ('grc', 'ix_llm_usage_records_tenant_id', 'ix_grc_llm_usage_records_tenant_id'),
            ('grc', 'ix_organisations_tenant_id', 'ix_grc_organisations_tenant_id'),
            ('grc', 'ix_outbox_messages_aggregate_id', 'ix_grc_outbox_messages_aggregate_id'),
            ('grc', 'ix_outbox_messages_status', 'ix_grc_outbox_messages_status'),
            ('grc', 'ix_outbox_messages_tenant_id', 'ix_grc_outbox_messages_tenant_id'),
            ('grc', 'ix_risk_analyses_batch_id', 'ix_grc_risk_mitigations_batch_id'),
            ('grc', 'ix_risk_analyses_org_id', 'ix_grc_risk_mitigations_org_id'),
            ('grc', 'ix_risk_analyses_source_of_truth_blob_id', 'ix_grc_risk_mitigations_blob_id'),
            ('grc', 'ix_risk_analyses_tenant_id', 'ix_grc_risk_mitigations_tenant_id'),
            ('grc', 'ix_source_of_truth_blobs_batch_id', 'ix_grc_blob_records_batch_id'),
            ('grc', 'ix_source_of_truth_blobs_content_hash', 'ix_grc_blob_records_content_hash'),
            ('grc', 'ix_source_of_truth_blobs_org_id', 'ix_grc_blob_records_org_id'),
            ('grc', 'ix_source_of_truth_blobs_processing_status', 'ix_grc_blob_records_processing_status'),
            ('grc', 'ix_source_of_truth_blobs_tenant_id', 'ix_grc_blob_records_tenant_id'),
            ('grc', 'ix_tenants_azure_tenant_id', 'ix_grc_tenants_azure_tenant_id'),
            ('grc', 'ix_tenants_stripe_customer_id', 'ix_grc_tenants_stripe_customer_id'),
            ('grc', 'ix_tenants_trial_ends_at', 'ix_grc_tenants_trial_ends_at'),
            ('grc', 'ix_user_roles_user_id', 'ix_grc_user_roles_user_id'),
            ('grc', 'ix_users_entra_object_id', 'ix_grc_users_entra_object_id'),
            ('grc', 'ix_users_last_active_at', 'ix_grc_users_last_active_at'),
            ('grc', 'ix_users_org_id', 'ix_grc_users_org_id'),
            ('grc', 'ix_users_tenant_id', 'ix_grc_users_tenant_id')
  ) AS t(sch, old_name, new_name) LOOP
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relname = r.old_name AND n.nspname = r.sch)
       AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relname = r.new_name AND n.nspname = r.sch) THEN
      EXECUTE format('ALTER INDEX %I.%I RENAME TO %I', r.sch, r.old_name, r.new_name);
    END IF;
  END LOOP;
END $$;
"""
    )


def _create_missing_indexes() -> None:
    """Indexes the models declare that no migration ever created.

    A few duplicate the leading column of an existing composite index
    (`ai_cost_records.tenant_id` under `ix_grc_ai_cost_records_tenant_date`, for
    example). They are created anyway: the models declare them, and leaving
    them out keeps the schema permanently out of step with the ORM.
    """
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_cost_records_provider"'
        ' ON grc."ai_cost_records" ("provider")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_cost_records_tenant_id"'
        ' ON grc."ai_cost_records" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_cost_records_usage_date"'
        ' ON grc."ai_cost_records" ("usage_date")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_risks_mitigation_status"'
        ' ON grc."ai_risks" ("mitigation_status")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_risks_severity" ON grc."ai_risks" ("severity")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_use_cases_data_classification"'
        ' ON grc."ai_use_cases" ("data_classification")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_use_cases_department"'
        ' ON grc."ai_use_cases" ("department")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_use_cases_discovered_via"'
        ' ON grc."ai_use_cases" ("discovered_via")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_ai_use_cases_status" ON grc."ai_use_cases" ("status")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_blob_records_source_type"'
        ' ON grc."blob_records" ("source_type")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_compliance_assessments_approval_status"'
        ' ON grc."compliance_assessments" ("approval_status")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_compliance_assessments_approved_by_id"'
        ' ON grc."compliance_assessments" ("approved_by_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_compliance_assessments_framework"'
        ' ON grc."compliance_assessments" ("framework")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_compliance_assessments_passed"'
        ' ON grc."compliance_assessments" ("passed")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_directory_users_linked_user_id"'
        ' ON grc."directory_users" ("linked_user_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_extension_device_heartbeats_tenant_id"'
        ' ON grc."extension_device_heartbeats" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_model_risk_profiles_category"'
        ' ON grc."model_risk_profiles" ("category")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_model_risk_profiles_lifecycle_status"'
        ' ON grc."model_risk_profiles" ("lifecycle_status")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_model_risk_profiles_provider"'
        ' ON grc."model_risk_profiles" ("provider")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_organisations_slug" ON grc."organisations" ("slug")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_tenant_drift_signals_tenant_id"'
        ' ON grc."tenant_drift_signals" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_tenant_handbook_overrides_tenant_id"'
        ' ON grc."tenant_handbook_overrides" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_use_case_reviews_tenant_id"'
        ' ON grc."use_case_reviews" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_vendor_assessment_imports_tenant_id"'
        ' ON grc."vendor_assessment_imports" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_vendor_assessment_imports_use_case_id"'
        ' ON grc."vendor_assessment_imports" ("use_case_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_vendor_assessment_requests_tenant_id"'
        ' ON grc."vendor_assessment_requests" ("tenant_id")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_grc_vendor_assessment_requests_use_case_id"'
        ' ON grc."vendor_assessment_requests" ("use_case_id")'
    )


def _apply_column_comments() -> None:
    """Column comments the models document but the migrations never applied."""
    op.execute(
        """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
            ('audit', 'audit_logs', 'details', 'JSON details of the event'),
            ('audit', 'audit_logs', 'event_type', 'auth.login, auth.logout, invite.created, role.changed, test.run, test.purge, etc.'),
            ('grc', 'ai_assets', 'deployment_status', 'active, shadow, deprecated, testing'),
            ('grc', 'ai_assets', 'eu_ai_act_risk_tier', 'Unacceptable Risk, High-Risk, Limited Risk, Minimal Risk'),
            ('grc', 'ai_assets', 'hosting_location', 'azure, aws, gcp, on-premise'),
            ('grc', 'ai_use_cases', 'created_at_in_use', 'Timestamp captured from discovery/registry source'),
            ('grc', 'blob_records', 'adapter_name', 'Name of the InputAdapter that created this record'),
            ('grc', 'blob_records', 'batch_id', 'Batch identifier for grouped processing'),
            ('grc', 'blob_records', 'content_hash', 'SHA-256 hash for deduplication'),
            ('grc', 'blob_records', 'embedding', 'OpenAI text-embedding-3-small vector'),
            ('grc', 'blob_records', 'metadata_json', 'Additional metadata as JSON string'),
            ('grc', 'blob_records', 'processing_status', 'pending, processing, processed, failed'),
            ('grc', 'blob_records', 'source_id', 'External source identifier'),
            ('grc', 'blob_records', 'source_type', 'Adapter source: manual, purview, defender'),
            ('grc', 'cloud_app_detections', 'risk_score', 'Defender risk score 0-10'),
            ('grc', 'compliance_assessments', 'framework', 'EU_AI_ACT, GDPR, NIST_AI_RMF'),
            ('grc', 'correlation_action_plans', 'action_steps', 'JSON array of action step objects'),
            ('grc', 'correlation_action_plans', 'correlation_type', 'pattern, escalation, dependency, compound'),
            ('grc', 'correlation_action_plans', 'priority', 'critical, high, medium, low'),
            ('grc', 'correlation_action_plans', 'reasoning', 'LLM reasoning chain'),
            ('grc', 'correlation_action_plans', 'risk_mitigation_ids', 'JSON array of risk_mitigation IDs that were correlated'),
            ('grc', 'correlation_action_plans', 'status', 'open, in_progress, resolved, dismissed'),
            ('grc', 'developer_events', 'occurred_at', 'When the event happened on the client (may differ from created_at).'),
            ('grc', 'directory_groups', 'group_type', 'Security | Microsoft365 | Distribution'),
            ('grc', 'directory_users', 'department', 'From Graph `department`; survey audience filters by this'),
            ('grc', 'directory_users', 'email', 'userPrincipalName or mail — lowercased'),
            ('grc', 'discovered_mcp_servers', 'transport', 'stdio, http, sse'),
            ('grc', 'dispatch_events', 'event_type', 'correlation_created, action_plan_updated, risk_escalated'),
            ('grc', 'dispatch_events', 'payload', 'JSON payload of the event'),
            ('grc', 'handbook_acknowledgements', 'version', 'Handbook version the user acknowledged'),
            ('grc', 'handbook_reminder_logs', 'version', 'Handbook version the user was reminded about'),
            ('grc', 'integrations', 'config_json', 'JSON object of provider-specific non-secret config'),
            ('grc', 'llm_usage_records', 'operation_type', 'risk_analysis, correlation, embedding, etc.'),
            ('grc', 'llm_usage_records', 'request_metadata', 'JSON metadata about the request'),
            ('grc', 'managed_devices', 'compliance_state', 'Vendor-specific compliance state. Intune: compliant | noncompliant | inGracePeriod | unknown. Jamf/Kandji/JumpCloud use their own sets — normalised to the same string column for downstream filtering.'),
            ('grc', 'outbox_messages', 'status', 'pending, processing, delivered, failed, dead_letter'),
            ('grc', 'purview_events', 'event_type', 'dlp_match | classification_change | label_applied | …'),
            ('grc', 'purview_events', 'raw_payload_json', 'Original Graph payload, for audit / debugging'),
            ('grc', 'purview_events', 'severity', 'low | medium | high | informational'),
            ('grc', 'risk_mitigations', 'citations', 'JSON array of {blob_id, snippet} references'),
            ('grc', 'risk_mitigations', 'confidence_score', 'Model confidence 0.0-1.0'),
            ('grc', 'risk_mitigations', 'created_by_job', 'Job ID that created this record'),
            ('grc', 'risk_mitigations', 'mitigation_steps', 'JSON array of step strings'),
            ('grc', 'risk_mitigations', 'risk_likelihood', 'very_likely, likely, possible, unlikely, rare'),
            ('grc', 'risk_mitigations', 'risk_severity', 'critical, high, medium, low, informational'),
            ('grc', 'slack_opt_outs', 'reason', 'Free-form, defaults to ''STOP reply'''),
            ('grc', 'slack_workspaces', 'bot_access_token_encrypted', NULL::text),
            ('grc', 'survey_deliveries', 'audience_filter_json', 'JSON {mode, values?} — see survey_audience.resolve'),
            ('grc', 'survey_responses', 'answers_json', 'JSON map of question_id → answer (string | list[str] | null)'),
            ('grc', 'survey_responses', 'recipient_email', 'Cached at dispatch time for audit; lower-cased.'),
            ('grc', 'survey_responses', 'slack_message_ts', 'ts of the most recent question DM — used by chat.delete on erasure'),
            ('grc', 'survey_responses', 'slack_user_id', 'Slack user id (Uxxxxx). Null for non-Slack recipients.'),
            ('grc', 'survey_templates', 'questions_json', 'JSON array of question definitions; see survey_flow.parse_questions'),
            ('grc', 'tenant_drift_signals', 'match_pattern', 'Case-insensitive substring matched against UseCase.tool.'),
            ('grc', 'tenant_drift_signals', 'successor_or_current_name', 'MODEL_DEPRECATION: successor model name. TOOL_REBRAND: current product name.'),
            ('grc', 'tenant_handbook_overrides', 'content_markdown', 'Markdown body of the tenant''s custom handbook content.'),
            ('grc', 'tenant_handbook_overrides', 'version', 'Tenant-specific handbook version. Bump to trigger re-ack campaign for all users in this tenant.'),
            ('grc', 'tenant_settings', 'custom_settings', 'JSON blob for additional settings'),
            ('grc', 'tenant_settings', 'llm_budget_usd', 'Monthly LLM budget in USD'),
            ('grc', 'tenant_settings', 'research_all_data', 'If true, correlation agent analyzes all accumulated data'),
            ('grc', 'use_cases', 'dispatched_from_response_id', 'FK to grc.survey_responses.id (added in PR B)')
  ) AS t(sch, tbl, col, txt) LOOP
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = r.sch AND table_name = r.tbl AND column_name = r.col) THEN
      EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS %L', r.sch, r.tbl, r.col, r.txt);
    END IF;
  END LOOP;
END $$;
"""
    )


def upgrade() -> None:
    _reconcile_pipeline_tables()
    op.execute(
        "ALTER TABLE grc.tenant_settings"
        " ADD COLUMN IF NOT EXISTS research_all_data BOOLEAN NOT NULL DEFAULT FALSE"
    )
    _rename_legacy_indexes()
    _create_missing_indexes()
    _apply_column_comments()


# ── downgrade ──────────────────────────────────────────────────────────


def downgrade() -> None:
    """Best effort. The columns dropped from correlation_action_plans and the
    whole of user_activity_blobs cannot be recovered — they come back empty."""
    op.execute(
        """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
            ('audit', 'audit_logs', 'details'),
            ('audit', 'audit_logs', 'event_type'),
            ('grc', 'ai_assets', 'deployment_status'),
            ('grc', 'ai_assets', 'eu_ai_act_risk_tier'),
            ('grc', 'ai_assets', 'hosting_location'),
            ('grc', 'ai_use_cases', 'created_at_in_use'),
            ('grc', 'blob_records', 'adapter_name'),
            ('grc', 'blob_records', 'batch_id'),
            ('grc', 'blob_records', 'content_hash'),
            ('grc', 'blob_records', 'embedding'),
            ('grc', 'blob_records', 'metadata_json'),
            ('grc', 'blob_records', 'processing_status'),
            ('grc', 'blob_records', 'source_id'),
            ('grc', 'blob_records', 'source_type'),
            ('grc', 'cloud_app_detections', 'risk_score'),
            ('grc', 'compliance_assessments', 'framework'),
            ('grc', 'correlation_action_plans', 'action_steps'),
            ('grc', 'correlation_action_plans', 'correlation_type'),
            ('grc', 'correlation_action_plans', 'priority'),
            ('grc', 'correlation_action_plans', 'reasoning'),
            ('grc', 'correlation_action_plans', 'risk_mitigation_ids'),
            ('grc', 'correlation_action_plans', 'status'),
            ('grc', 'developer_events', 'occurred_at'),
            ('grc', 'directory_groups', 'group_type'),
            ('grc', 'directory_users', 'department'),
            ('grc', 'directory_users', 'email'),
            ('grc', 'discovered_mcp_servers', 'transport'),
            ('grc', 'dispatch_events', 'event_type'),
            ('grc', 'dispatch_events', 'payload'),
            ('grc', 'handbook_acknowledgements', 'version'),
            ('grc', 'handbook_reminder_logs', 'version'),
            ('grc', 'integrations', 'config_json'),
            ('grc', 'llm_usage_records', 'operation_type'),
            ('grc', 'llm_usage_records', 'request_metadata'),
            ('grc', 'managed_devices', 'compliance_state'),
            ('grc', 'outbox_messages', 'status'),
            ('grc', 'purview_events', 'event_type'),
            ('grc', 'purview_events', 'raw_payload_json'),
            ('grc', 'purview_events', 'severity'),
            ('grc', 'risk_mitigations', 'citations'),
            ('grc', 'risk_mitigations', 'confidence_score'),
            ('grc', 'risk_mitigations', 'created_by_job'),
            ('grc', 'risk_mitigations', 'mitigation_steps'),
            ('grc', 'risk_mitigations', 'risk_likelihood'),
            ('grc', 'risk_mitigations', 'risk_severity'),
            ('grc', 'slack_opt_outs', 'reason'),
            ('grc', 'slack_workspaces', 'bot_access_token_encrypted'),
            ('grc', 'survey_deliveries', 'audience_filter_json'),
            ('grc', 'survey_responses', 'answers_json'),
            ('grc', 'survey_responses', 'recipient_email'),
            ('grc', 'survey_responses', 'slack_message_ts'),
            ('grc', 'survey_responses', 'slack_user_id'),
            ('grc', 'survey_templates', 'questions_json'),
            ('grc', 'tenant_drift_signals', 'match_pattern'),
            ('grc', 'tenant_drift_signals', 'successor_or_current_name'),
            ('grc', 'tenant_handbook_overrides', 'content_markdown'),
            ('grc', 'tenant_handbook_overrides', 'version'),
            ('grc', 'tenant_settings', 'custom_settings'),
            ('grc', 'tenant_settings', 'llm_budget_usd'),
            ('grc', 'tenant_settings', 'research_all_data'),
            ('grc', 'use_cases', 'dispatched_from_response_id')
  ) AS t(sch, tbl, col) LOOP
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = r.sch AND table_name = r.tbl AND column_name = r.col) THEN
      EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS NULL', r.sch, r.tbl, r.col);
    END IF;
  END LOOP;
END $$;
"""
    )
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_cost_records_provider"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_cost_records_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_cost_records_usage_date"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_risks_mitigation_status"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_risks_severity"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_use_cases_data_classification"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_use_cases_department"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_use_cases_discovered_via"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_ai_use_cases_status"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_blob_records_source_type"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_compliance_assessments_approval_status"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_compliance_assessments_approved_by_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_compliance_assessments_framework"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_compliance_assessments_passed"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_directory_users_linked_user_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_extension_device_heartbeats_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_model_risk_profiles_category"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_model_risk_profiles_lifecycle_status"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_model_risk_profiles_provider"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_organisations_slug"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_tenant_drift_signals_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_tenant_handbook_overrides_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_use_case_reviews_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_vendor_assessment_imports_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_vendor_assessment_imports_use_case_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_vendor_assessment_requests_tenant_id"')
    op.execute('DROP INDEX IF EXISTS grc."ix_grc_vendor_assessment_requests_use_case_id"')
    op.execute(
        """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
            ('audit', 'ix_audit_logs_actor_id', 'ix_audit_audit_logs_actor_id'),
            ('audit', 'ix_audit_logs_event_type', 'ix_audit_audit_logs_event_type'),
            ('audit', 'ix_audit_logs_tenant_id', 'ix_audit_audit_logs_tenant_id'),
            ('audit', 'ix_audit_logs_timestamp', 'ix_audit_audit_logs_timestamp'),
            ('grc', 'ix_api_keys_user_id', 'ix_grc_api_keys_user_id'),
            ('grc', 'ix_dev_events_api_key_id', 'ix_grc_developer_events_api_key_id'),
            ('grc', 'ix_dev_events_correlation_id', 'ix_grc_developer_events_correlation_id'),
            ('grc', 'ix_dev_events_event_type', 'ix_grc_developer_events_event_type'),
            ('grc', 'ix_dev_events_occurred_at', 'ix_grc_developer_events_occurred_at'),
            ('grc', 'ix_dev_events_session_id', 'ix_grc_developer_events_session_id'),
            ('grc', 'ix_dev_events_severity', 'ix_grc_developer_events_severity'),
            ('grc', 'ix_dev_events_source', 'ix_grc_developer_events_source'),
            ('grc', 'ix_dev_events_tenant_id', 'ix_grc_developer_events_tenant_id'),
            ('grc', 'ix_developer_api_key_scopes_api_key_id', 'ix_grc_developer_api_key_scopes_api_key_id'),
            ('grc', 'ix_dispatch_events_correlation_id', 'ix_grc_dispatch_events_correlation_id'),
            ('grc', 'ix_dispatch_events_org_id', 'ix_grc_dispatch_events_org_id'),
            ('grc', 'ix_dispatch_events_tenant_id', 'ix_grc_dispatch_events_tenant_id'),
            ('grc', 'ix_event_defs_name', 'ix_grc_developer_event_definitions_name'),
            ('grc', 'ix_event_defs_tenant_id', 'ix_grc_developer_event_definitions_tenant_id'),
            ('grc', 'ix_governance_correlations_batch_id', 'ix_grc_correlation_action_plans_batch_id'),
            ('grc', 'ix_governance_correlations_org_id', 'ix_grc_correlation_action_plans_org_id'),
            ('grc', 'ix_governance_correlations_tenant_id', 'ix_grc_correlation_action_plans_tenant_id'),
            ('grc', 'ix_grc_agent_control_states_tenant', 'ix_grc_agent_control_states_tenant_id'),
            ('grc', 'ix_grc_ai_cost_records_integration', 'ix_grc_ai_cost_records_integration_id'),
            ('grc', 'ix_grc_cad_app_domain', 'ix_grc_cloud_app_detections_app_domain'),
            ('grc', 'ix_grc_cad_app_name', 'ix_grc_cloud_app_detections_app_name'),
            ('grc', 'ix_grc_cad_external_detection_id', 'ix_grc_cloud_app_detections_external_detection_id'),
            ('grc', 'ix_grc_cad_integration_id', 'ix_grc_cloud_app_detections_integration_id'),
            ('grc', 'ix_grc_cad_last_seen_at', 'ix_grc_cloud_app_detections_last_seen_at'),
            ('grc', 'ix_grc_cad_tenant_id', 'ix_grc_cloud_app_detections_tenant_id'),
            ('grc', 'ix_grc_cad_user_upn', 'ix_grc_cloud_app_detections_user_principal_name'),
            ('grc', 'ix_grc_discovered_agents_tenant', 'ix_grc_discovered_agents_tenant_id'),
            ('grc', 'ix_grc_discovered_defender_apps_tenant', 'ix_grc_discovered_defender_apps_tenant_id'),
            ('grc', 'ix_grc_discovered_mcp_servers_tenant', 'ix_grc_discovered_mcp_servers_tenant_id'),
            ('grc', 'ix_grc_extension_heartbeats_fingerprint', 'ix_grc_extension_device_heartbeats_device_fingerprint'),
            ('grc', 'ix_grc_extension_heartbeats_last_seen', 'ix_grc_extension_device_heartbeats_last_seen_at'),
            ('grc', 'ix_grc_extension_heartbeats_user_email', 'ix_grc_extension_device_heartbeats_user_email'),
            ('grc', 'ix_grc_handbook_tenant_id', 'ix_grc_handbook_acknowledgements_tenant_id'),
            ('grc', 'ix_grc_handbook_user_id', 'ix_grc_handbook_acknowledgements_user_id'),
            ('grc', 'ix_grc_intune_devices_enrolled_upn', 'ix_grc_managed_devices_enrolled_user_email'),
            ('grc', 'ix_grc_intune_devices_external_device_id', 'ix_grc_managed_devices_external_device_id'),
            ('grc', 'ix_grc_intune_devices_integration_id', 'ix_grc_managed_devices_integration_id'),
            ('grc', 'ix_grc_intune_devices_tenant_id', 'ix_grc_managed_devices_tenant_id'),
            ('grc', 'ix_grc_purview_events_actor_upn', 'ix_grc_purview_events_actor_user_principal_name'),
            ('grc', 'ix_grc_saas_vendor_profiles_tenant', 'ix_grc_saas_vendor_profiles_tenant_id'),
            ('grc', 'ix_invites_email', 'ix_grc_invites_email'),
            ('grc', 'ix_llm_usage_records_job_id', 'ix_grc_llm_usage_records_job_id'),
            ('grc', 'ix_llm_usage_records_org_id', 'ix_grc_llm_usage_records_org_id'),
            ('grc', 'ix_llm_usage_records_tenant_id', 'ix_grc_llm_usage_records_tenant_id'),
            ('grc', 'ix_organisations_tenant_id', 'ix_grc_organisations_tenant_id'),
            ('grc', 'ix_outbox_messages_aggregate_id', 'ix_grc_outbox_messages_aggregate_id'),
            ('grc', 'ix_outbox_messages_status', 'ix_grc_outbox_messages_status'),
            ('grc', 'ix_outbox_messages_tenant_id', 'ix_grc_outbox_messages_tenant_id'),
            ('grc', 'ix_risk_analyses_batch_id', 'ix_grc_risk_mitigations_batch_id'),
            ('grc', 'ix_risk_analyses_org_id', 'ix_grc_risk_mitigations_org_id'),
            ('grc', 'ix_risk_analyses_source_of_truth_blob_id', 'ix_grc_risk_mitigations_blob_id'),
            ('grc', 'ix_risk_analyses_tenant_id', 'ix_grc_risk_mitigations_tenant_id'),
            ('grc', 'ix_source_of_truth_blobs_batch_id', 'ix_grc_blob_records_batch_id'),
            ('grc', 'ix_source_of_truth_blobs_content_hash', 'ix_grc_blob_records_content_hash'),
            ('grc', 'ix_source_of_truth_blobs_org_id', 'ix_grc_blob_records_org_id'),
            ('grc', 'ix_source_of_truth_blobs_processing_status', 'ix_grc_blob_records_processing_status'),
            ('grc', 'ix_source_of_truth_blobs_tenant_id', 'ix_grc_blob_records_tenant_id'),
            ('grc', 'ix_tenants_azure_tenant_id', 'ix_grc_tenants_azure_tenant_id'),
            ('grc', 'ix_tenants_stripe_customer_id', 'ix_grc_tenants_stripe_customer_id'),
            ('grc', 'ix_tenants_trial_ends_at', 'ix_grc_tenants_trial_ends_at'),
            ('grc', 'ix_user_roles_user_id', 'ix_grc_user_roles_user_id'),
            ('grc', 'ix_users_entra_object_id', 'ix_grc_users_entra_object_id'),
            ('grc', 'ix_users_last_active_at', 'ix_grc_users_last_active_at'),
            ('grc', 'ix_users_org_id', 'ix_grc_users_org_id'),
            ('grc', 'ix_users_tenant_id', 'ix_grc_users_tenant_id')
  ) AS t(sch, old_name, new_name) LOOP
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relname = r.new_name AND n.nspname = r.sch)
       AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relname = r.old_name AND n.nspname = r.sch) THEN
      EXECUTE format('ALTER INDEX %I.%I RENAME TO %I', r.sch, r.new_name, r.old_name);
    END IF;
  END LOOP;
END $$;
"""
    )
    op.execute("ALTER TABLE grc.tenant_settings DROP COLUMN IF EXISTS research_all_data")

    _rename_constraint(
        "dispatch_events",
        "fk_dispatch_events_correlation_id_correlation_action_plans",
        "fk_dispatch_events_correlation_id_governance_correlations",
    )
    for col, ddl in (
        ("user_activity_blob_ids", "TEXT NOT NULL DEFAULT ''"),
        ("score_reliability", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
        ("match_tags", "TEXT NOT NULL DEFAULT ''"),
        ("is_excluded", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("excluded_by", "UUID"),
        ("excluded_at", "TIMESTAMPTZ"),
        ("exclusion_reason", "TEXT"),
    ):
        op.execute(f"ALTER TABLE grc.correlation_action_plans ADD COLUMN IF NOT EXISTS {col} {ddl}")
    _rename_column("correlation_action_plans", "risk_mitigation_ids", "risk_analysis_ids")
    _rename_table("correlation_action_plans", "governance_correlations")

    _rename_column("risk_mitigations", "blob_id", "source_of_truth_blob_id")
    _rename_table("risk_mitigations", "risk_analyses")

    op.execute("ALTER TABLE grc.blob_records ALTER COLUMN title SET NOT NULL")
    op.execute("ALTER TABLE grc.blob_records ALTER COLUMN source_type TYPE VARCHAR(100)")
    op.execute("ALTER TABLE grc.blob_records DROP COLUMN IF EXISTS adapter_name")
    op.execute("ALTER TABLE grc.blob_records DROP COLUMN IF EXISTS source_id")
    _rename_column("blob_records", "created_by", "reported_by")
    _rename_column("blob_records", "source_type", "source_category")
    _rename_table("blob_records", "source_of_truth_blobs")
