// Prompt Shields data connector tile for Microsoft Sentinel — v2.
//
// This is the last open item in the spec, and it does *not* use the Codeless
// Connector Platform the spec §5 decision point proposed. CCP is a polling
// platform: its one connector kind is RestApiPoller, and it exists so Sentinel
// can periodically fetch from a vendor's API. Prompt Shields pushes through
// the Azure Monitor Logs Ingestion API. Adopting CCP would mean inverting the
// whole delivery model — exposing a customer-authenticated polling API,
// re-solving batching and retry on Microsoft's schedule instead of ours, and
// running two ingestion paths at once.
//
// What the spec actually wanted from CCP was the UX: "install Prompt Shields
// from Content Hub and have the connection wired automatically". A `Static`
// data connector definition delivers exactly that half without the polling —
// the tile in the Data connectors gallery, the setup instructions, the
// ingestion graph, sample queries, and a live connected/disconnected state
// driven by a KQL query over the custom table. Per Microsoft's own reference,
// `kind: Customizable` is for API polling connectors and `Static` is for the
// rest, with `isConnectedQuery` the connectivity criteria for the latter.
//
// A tile is not cosmetic here. Without one, "is Prompt Shields actually
// delivering?" is a question the customer's SOC can only answer by knowing our
// table name and writing a query. With one, it is the same green/grey dot as
// every other source they run.
//
// The definition lives in sentinel-data-connector.json so the KQL has one
// source of truth a test can validate against sentinel_schema, matching the
// analytic rules, workbook and ASIM parser. Azure validates none of it: a
// connectivity query naming a column we do not emit leaves the tile
// permanently grey while data flows in perfectly.
//
// Deploy from the infra/ directory so loadJsonContent resolves:
//   az deployment group create \
//     --resource-group <rg-with-the-sentinel-workspace> \
//     --template-file sentinel-data-connector.bicep \
//     --parameters workspaceName=<workspace>

@description('Name of the Sentinel-enabled Log Analytics workspace.')
param workspaceName string

var connector = loadJsonContent('./sentinel-data-connector.json')

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: workspaceName
}

resource connectorDefinition 'Microsoft.SecurityInsights/dataConnectorDefinitions@2022-09-01-preview' = {
  scope: workspace
  // Stable id so a redeploy updates the tile in place rather than adding a
  // second one, the same naming stance as the rules, workbook and parser.
  name: connector.connectorId
  kind: connector.kind
  properties: {
    connectorUiConfig: {
      id: connector.connectorId
      title: connector.title
      publisher: connector.publisher
      descriptionMarkdown: connector.descriptionMarkdown
      graphQueriesTableName: connector.graphQueriesTableName
      connectivityCriteria: connector.connectivityCriteria
      dataTypes: connector.dataTypes
      graphQueries: connector.graphQueries
      sampleQueries: connector.sampleQueries
      permissions: connector.permissions
      instructionSteps: connector.instructionSteps
      availability: connector.availability
    }
  }
}

output connectorId string = connector.connectorId
output connectorKind string = connector.kind
output connectorTableName string = connector.graphQueriesTableName
