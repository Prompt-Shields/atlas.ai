// ============================================================================
// Prompt Shields → Microsoft Sentinel — customer-side setup
// ============================================================================
//
// The CUSTOMER runs this in THEIR Azure subscription, against the resource
// group holding their Sentinel-enabled Log Analytics workspace. It creates
// everything the Prompt Shields forwarder needs to write:
//
//   * the custom table  PromptShieldsActivity_CL
//   * a Data Collection Endpoint (DCE)
//   * a Data Collection Rule (DCR) declaring the Custom-PromptShields_v1 stream
//   * a Monitoring Metrics Publisher role assignment, scoped to that DCR only
//
// The column list is a transcription of
// docs/integrations/microsoft-sentinel/data-schema.md, which is canonical.
// backend/app/services/sentinel_schema.py is the third copy — all three must
// change together or Azure Monitor silently drops the new column.
//
// Usage (Azure Cloud Shell):
//
//   az deployment group create \
//     --resource-group <rg-with-the-sentinel-workspace> \
//     --template-file sentinel-customer-setup.bicep \
//     --parameters workspaceName=<workspace> \
//                  forwarderPrincipalId=<app-registration-object-id>
//
// Then paste the deployment outputs into Prompt Shields:
// Integrations → Microsoft Sentinel. See
// docs/integrations/microsoft-sentinel/runbooks/customer-onboarding.md.
// ============================================================================

@description('Name of the existing Sentinel-enabled Log Analytics workspace.')
param workspaceName string

@description('Location for the DCE and DCR. Must match the workspace region.')
param location string = resourceGroup().location

@description('Object id of the service principal Prompt Shields authenticates as. Find it with: az ad sp show --id <clientId> --query id -o tsv')
param forwarderPrincipalId string

@description('Name prefix for the created DCE/DCR.')
param namePrefix string = 'promptshields'

@description('Tags applied to created resources.')
param tags object = {}

// Ingestion is billed per GB by Microsoft, not by us. A typical 80-user
// deployment is ~22 MB/year — see spec.md §8, open question 7.
@description('Retention for the custom table, in days.')
@minValue(4)
@maxValue(730)
param retentionInDays int = 90

var dceName = '${namePrefix}-dce'
var dcrName = '${namePrefix}-dcr'
var tableName = 'PromptShieldsActivity_CL'
var streamName = 'Custom-PromptShields_v1'

// Monitoring Metrics Publisher. Assigned on the DCR alone rather than the
// subscription, so a leaked Prompt Shields credential can write to this one
// stream and nothing else.
var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

// ── Custom table ─────────────────────────────────────────────────────
// Column order and types mirror data-schema.md. TimeGenerated is required by
// Sentinel for every table. There is deliberately no prompt-body column: see
// the "Explicit non-goals" section of that document before adding one.

resource customTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: tableName
  properties: {
    totalRetentionInDays: retentionInDays
    plan: 'Analytics'
    schema: {
      name: tableName
      columns: [
        { name: 'TimeGenerated', type: 'datetime' }
        { name: 'EventId', type: 'string' }
        { name: 'TenantId', type: 'string' }
        { name: 'User', type: 'string' }
        { name: 'UserAadObjectId', type: 'string' }
        { name: 'Department', type: 'string' }
        { name: 'AiTool', type: 'string' }
        { name: 'IsShadowAi', type: 'boolean' }
        { name: 'EventType', type: 'string' }
        { name: 'SensitiveType', type: 'string' }
        { name: 'Severity', type: 'string' }
        { name: 'Detail', type: 'string' }
        { name: 'PolicyId', type: 'string' }
        { name: 'PolicyName', type: 'string' }
        { name: 'EndpointId', type: 'string' }
        { name: 'EndpointPlatform', type: 'string' }
        { name: 'RedactionTokenCount', type: 'int' }
        { name: 'PromptHash', type: 'string' }
      ]
    }
  }
}

// ── Data Collection Endpoint ─────────────────────────────────────────
// The HTTPS endpoint the forwarder POSTs batches to.

resource dce 'Microsoft.Insights/dataCollectionEndpoints@2023-03-11' = {
  name: dceName
  location: location
  tags: tags
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// ── Data Collection Rule ─────────────────────────────────────────────
// Declares the inbound stream and routes it to the custom table. The
// transformKql is pass-through; a customer who wants to drop low-severity
// events to save ingestion cost can edit it, e.g.
//   source | where Severity != "Low"

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: dcrName
  location: location
  tags: tags
  dependsOn: [customTable]
  properties: {
    dataCollectionEndpointId: dce.id
    streamDeclarations: {
      '${streamName}': {
        columns: [
          { name: 'TimeGenerated', type: 'datetime' }
          { name: 'EventId', type: 'string' }
          { name: 'TenantId', type: 'string' }
          { name: 'User', type: 'string' }
          { name: 'UserAadObjectId', type: 'string' }
          { name: 'Department', type: 'string' }
          { name: 'AiTool', type: 'string' }
          { name: 'IsShadowAi', type: 'boolean' }
          { name: 'EventType', type: 'string' }
          { name: 'SensitiveType', type: 'string' }
          { name: 'Severity', type: 'string' }
          { name: 'Detail', type: 'string' }
          { name: 'PolicyId', type: 'string' }
          { name: 'PolicyName', type: 'string' }
          { name: 'EndpointId', type: 'string' }
          { name: 'EndpointPlatform', type: 'string' }
          { name: 'RedactionTokenCount', type: 'int' }
          { name: 'PromptHash', type: 'string' }
        ]
      }
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: workspace.id
          name: 'sentinelWorkspace'
        }
      ]
    }
    dataFlows: [
      {
        streams: [streamName]
        destinations: ['sentinelWorkspace']
        transformKql: 'source'
        outputStream: 'Custom-${tableName}'
      }
    ]
  }
}

// ── Role assignment ──────────────────────────────────────────────────
// Scoped to the DCR, not the resource group or subscription.

resource publisherAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: dcr
  name: guid(dcr.id, forwarderPrincipalId, monitoringMetricsPublisherRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      monitoringMetricsPublisherRoleId
    )
    principalId: forwarderPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ──────────────────────────────────────────────────────────
// These four values go into the Prompt Shields connect wizard, alongside the
// customer's Azure AD tenant id and the app registration's client id/secret.

@description('Paste into "Data Collection Endpoint URI".')
output dceUrl string = dce.properties.logsIngestion.endpoint

@description('Paste into "DCR immutable id".')
output dcrImmutableId string = dcr.properties.immutableId

@description('Paste into "Stream name".')
output streamNameOut string = streamName

@description('Paste into "Target custom table".')
output tableNameOut string = tableName
