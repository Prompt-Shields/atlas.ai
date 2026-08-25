// ============================================================================
// API Management — Gateway with JWT, rate limits, and security policies
// ============================================================================

param prefix string
param location string
param tags object
param environment string
param backendUrl string
param appInsightsInstrumentationKey string

var skuName = environment == 'prod' ? 'Standard' : 'Developer'

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: '${prefix}-apim'
  location: location
  tags: tags
  sku: {
    name: skuName
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: 'admin@aigrc.com'
    publisherName: 'AI-GRC Platform'
  }
}

// ── Logger ───────────────────────────────────────────────────────────
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2023-09-01-preview' = {
  name: 'appinsights-logger'
  parent: apim
  properties: {
    loggerType: 'applicationInsights'
    credentials: {
      instrumentationKey: appInsightsInstrumentationKey
    }
  }
}

// ── Backend API ──────────────────────────────────────────────────────
resource backendApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  name: 'aigrc-api'
  parent: apim
  properties: {
    displayName: 'AI-GRC API'
    path: 'api/v1'
    protocols: ['https']
    serviceUrl: backendUrl
    subscriptionRequired: false
  }
}

// ── Global Policy ────────────────────────────────────────────────────
resource globalPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  name: 'policy'
  parent: backendApi
  properties: {
    value: loadTextContent('../apim-policies/global-policy.xml')
    format: 'xml'
  }
}

output gatewayUrl string = apim.properties.gatewayUrl
output apimName string = apim.name
