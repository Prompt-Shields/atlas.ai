# atlas.ai documentation

Start with the [repository README](../README.md) for setup and the tenancy rules.

## Reference

| Doc | Covers |
|---|---|
| [`auth-entra-sso.md`](./auth-entra-sso.md) | Microsoft Entra ID SSO, password sign-up, and self-serve tenant creation |
| [`policy-enforcement-admin-guide.md`](./policy-enforcement-admin-guide.md) | How an admin uses policy enforcement end to end |

## Design

| Doc | Covers |
|---|---|
| [`design/prompt-telemetry.md`](./design/prompt-telemetry.md) | Prompt telemetry ingestion and storage model |
| [`design/ai-cost-ledger-connectors.md`](./design/ai-cost-ledger-connectors.md) | AI cost ledger and provider connectors |
| [`design/ai-spm-merge.md`](./design/ai-spm-merge.md) | AI-SPM domain model, tenancy, and auth design |
| [`design/managed-device-unification.md`](./design/managed-device-unification.md) | Unifying managed-device records across MDM sources |

## Integrations

| Doc | Covers |
|---|---|
| [`integrations/microsoft-sentinel/spec.md`](./integrations/microsoft-sentinel/spec.md) | Microsoft Sentinel integration scope and architecture |
| [`integrations/microsoft-sentinel/data-schema.md`](./integrations/microsoft-sentinel/data-schema.md) | `PromptShieldsActivity_CL` column schema |
| [`integrations/microsoft-sentinel/kql-samples.md`](./integrations/microsoft-sentinel/kql-samples.md) | Starter KQL queries for customer SOCs |
| [`integrations/microsoft-sentinel/spec.md#asim-parser-shipped`](./integrations/microsoft-sentinel/spec.md) | ASIM AuditEvent parser — mapping decisions and rationale |
| [`integrations/microsoft-sentinel/runbooks/customer-onboarding.md`](./integrations/microsoft-sentinel/runbooks/customer-onboarding.md) | Checklist for onboarding a customer onto the forwarder |
| [`integrations/self-hosted-ai-spend/reporting-usage.md`](./integrations/self-hosted-ai-spend/reporting-usage.md) | Reporting spend from customer-hosted AI apps (Foundry / Bedrock / self-hosted) |

## Platform specification

[`../spec.md`](../spec.md) — the top-level platform spec: components, data flow,
and the analysis pipeline.
