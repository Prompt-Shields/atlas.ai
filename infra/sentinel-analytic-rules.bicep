// ============================================================================
// Prompt Shields → Microsoft Sentinel — scheduled analytics rules (v1.1)
// ============================================================================
//
// Turns rows in PromptShieldsActivity_CL into Sentinel **incidents**, so
// high-severity AI activity lands in the analyst queue instead of sitting in a
// log table nobody queries.
//
// The CUSTOMER runs this in THEIR subscription, against the resource group
// holding their Sentinel-enabled workspace, AFTER sentinel-customer-setup.bicep
// has created the table and events are flowing. Deploying it against an empty
// table is harmless — the rules simply never fire.
//
// Rule definitions live in sentinel-analytic-rules.json and are loaded here
// verbatim. That file is the single source of truth: it is also validated by
// backend/tests/unit/test_sentinel_analytic_rules.py against
// backend/app/services/sentinel_schema.py, so a rule cannot reference a column
// the forwarder does not emit. A rule that does would deploy cleanly, never
// fire, and leave the SOC believing it was covered.
//
// Why rules rather than the Graph Security alerts channel the spec originally
// proposed: third-party alert *creation* through the Graph Security API is not
// a supported path — the legacy alerts API is deprecated with a retirement
// date announced, and the v2 API is read-oriented. Scheduled analytics rules
// are the supported mechanism for a solution to raise Sentinel incidents from
// its own custom table. See spec.md §2.
//
// Usage (Azure Cloud Shell), from the infra/ directory so the JSON resolves:
//
//   az deployment group create \
//     --resource-group <rg-with-the-sentinel-workspace> \
//     --template-file sentinel-analytic-rules.bicep \
//     --parameters workspaceName=<workspace>
//
// To disable a rule without deleting it, set `enabled: false` on it in the
// JSON and redeploy — the rule name is derived from its stable `id`, so a
// redeploy updates in place rather than creating a duplicate.
// ============================================================================

@description('Name of the existing Sentinel-enabled Log Analytics workspace.')
param workspaceName string

@description('Set false to deploy every rule in a disabled state — useful for a dry run in a production workspace before letting rules page anyone.')
param rulesEnabled bool = true

var ruleFile = loadJsonContent('./sentinel-analytic-rules.json')
var rules = ruleFile.rules

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

// Sentinel alert rules are extension resources on the workspace. The name is a
// deterministic GUID derived from the workspace id and the rule's stable `id`,
// so redeploying updates each rule in place instead of accumulating copies.
resource analyticRules 'Microsoft.SecurityInsights/alertRules@2023-02-01' = [
  for rule in rules: {
    scope: workspace
    name: guid(workspace.id, rule.id)
    kind: 'Scheduled'
    properties: {
      displayName: rule.displayName
      description: rule.description
      severity: rule.severity
      enabled: rulesEnabled && rule.enabled
      query: rule.query
      queryFrequency: rule.queryFrequency
      queryPeriod: rule.queryPeriod
      triggerOperator: rule.triggerOperator
      triggerThreshold: rule.triggerThreshold
      suppressionDuration: rule.suppressionDuration
      suppressionEnabled: rule.suppressionEnabled
      tactics: rule.tactics
      eventGroupingSettings: rule.eventGroupingSettings
      entityMappings: rule.entityMappings
      incidentConfiguration: rule.incidentConfiguration
    }
  }
]

@description('Display names of the rules deployed, for the onboarding checklist.')
output deployedRules array = [for (rule, i) in rules: rule.displayName]

@description('How many rules this deployment manages.')
output ruleCount int = length(rules)
