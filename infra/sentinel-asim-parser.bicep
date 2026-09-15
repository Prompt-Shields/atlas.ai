// ASIM AuditEvent parser for PromptShieldsActivity_CL — Sentinel v2.
//
// Deploys the parser as a workspace savedSearch, which is how a solution
// publishes an ASIM parser. Once installed, a customer's existing ASIM queries
// reach Prompt Shields activity without knowing our table exists:
//
//   imAuditEvent | where EventProduct == 'Prompt Shields'
//
// and, more to the point, an ASIM query that does *not* name us at all —
// "every audit event where the actor is this user" — starts including AI
// policy decisions alongside their Exchange and Azure Activity events. That is
// the whole reason large enterprises ask for a parser: without one, our table
// is a silo their existing detections cannot see.
//
// The definition lives in sentinel-asim-parser.json so the KQL has one source
// of truth that a test can validate against sentinel_schema, the same
// arrangement as the analytic rules and the workbook. Azure will not tell you
// that a parser emits a wrong field name or a non-ASIM enum value — the parser
// deploys, runs, and returns rows whose columns quietly do not conform.
//
// Naming follows the ASIM convention for a source-specific filtering parser,
// vim<Schema><Vendor><Product>. The unifying parser imAuditEvent calls
// parsers with exactly this signature, so a customer can add ours to a custom
// unifying parser without adapting it.
//
// Deploy from the infra/ directory so loadJsonContent resolves:
//   az deployment group create \
//     --resource-group <rg-with-the-sentinel-workspace> \
//     --template-file sentinel-asim-parser.bicep \
//     --parameters workspaceName=<workspace>

@description('Name of the Sentinel-enabled Log Analytics workspace.')
param workspaceName string

var parser = loadJsonContent('./sentinel-asim-parser.json')

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: workspaceName
}

resource asimParser 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  // Deterministic name so a redeploy updates the parser in place rather than
  // stacking copies, matching how the rules and workbook are named.
  name: parser.functionAlias
  properties: {
    etag: '*'
    displayName: parser.displayName
    category: parser.category
    functionAlias: parser.functionAlias
    functionParameters: parser.functionParameters
    query: parser.query
    version: parser.version
  }
}

output parserFunctionAlias string = parser.functionAlias
output parserSchema string = parser.eventSchema
output parserSchemaVersion string = parser.eventSchemaVersion
