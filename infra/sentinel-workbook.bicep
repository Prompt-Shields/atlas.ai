// ============================================================================
// Prompt Shields → Microsoft Sentinel — workbook (v1.1)
// ============================================================================
//
// The in-Sentinel dashboard over PromptShieldsActivity_CL: what is being
// caught, in which tools, for whom. Complements the analytics rules — the
// rules tell the SOC when to look, this tells them what they are looking at.
//
// The CUSTOMER runs this in THEIR subscription, against the resource group
// holding their Sentinel-enabled workspace, AFTER sentinel-customer-setup.bicep
// has created the table. Deploying against an empty table is harmless: every
// tile simply renders empty until events arrive.
//
// The workbook body lives in sentinel-workbook.json and is loaded verbatim.
// That file is validated by backend/tests/unit/test_sentinel_workbook.py
// against backend/app/services/sentinel_schema.py, so no tile can query a
// column the forwarder does not emit. Such a tile renders as a permanently
// empty chart with no error anywhere — the customer would simply believe they
// had no shadow AI.
//
// Usage (Azure Cloud Shell), from the infra/ directory so the JSON resolves:
//
//   az deployment group create \
//     --resource-group <rg-with-the-sentinel-workspace> \
//     --template-file sentinel-workbook.bicep \
//     --parameters workspaceName=<workspace>
//
// Then open Sentinel → Workbooks → "Prompt Shields — AI activity".
// ============================================================================

@description('Name of the existing Sentinel-enabled Log Analytics workspace.')
param workspaceName string

@description('Location for the workbook resource. Defaults to the resource group.')
param location string = resourceGroup().location

@description('Name shown in the Sentinel workbook gallery.')
param workbookDisplayName string = 'Prompt Shields — AI activity'

@description('Tags applied to the workbook.')
param tags object = {}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

// `serializedData` is a string, not an object — the portal stores the whole
// notebook as escaped JSON. loadJsonContent + string() keeps the source file
// readable and diffable rather than a one-line blob.
resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  // Deterministic name so a redeploy updates the workbook in place instead of
  // stacking near-identical copies in the customer's gallery.
  name: guid(workspace.id, 'promptshields-ai-activity-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: workbookDisplayName
    serializedData: string(loadJsonContent('./sentinel-workbook.json'))
    version: '1.0'
    sourceId: workspace.id
    // 'sentinel' puts it in the Sentinel workbook gallery rather than the
    // generic Azure Monitor one.
    category: 'sentinel'
  }
}

@description('Resource id of the deployed workbook.')
output workbookId string = workbook.id

@description('Name to look for in Sentinel → Workbooks.')
output workbookName string = workbookDisplayName
