// ============================================================================
// AI-GRC Platform — Main Bicep Template
// Deploys all Azure resources for the platform
// ============================================================================

targetScope = 'resourceGroup'

@description('Environment name')
@allowed(['dev', 'prod'])
param environment string

@description('Azure region')
param location string = resourceGroup().location

@description('Project name prefix')
param projectName string = 'aigrc'

@description('Container image tag')
param imageTag string = 'latest'

@description('ACR login server')
param acrLoginServer string

// ── Variables ────────────────────────────────────────────────────────
var prefix = '${projectName}-${environment}'
var tags = {
  project: projectName
  environment: environment
  managedBy: 'bicep'
}

// ── Modules ──────────────────────────────────────────────────────────

module monitoring 'modules/monitoring.bicep' = {
  name: '${prefix}-monitoring'
  params: {
    prefix: prefix
    location: location
    tags: tags
  }
}

module keyVault 'modules/key-vault.bicep' = {
  name: '${prefix}-keyvault'
  params: {
    prefix: prefix
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

module database 'modules/cosmos-postgres.bicep' = {
  name: '${prefix}-database'
  params: {
    prefix: prefix
    location: location
    tags: tags
    environment: environment
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: '${prefix}-container-apps'
  params: {
    prefix: prefix
    location: location
    tags: tags
    environment: environment
    imageTag: imageTag
    acrLoginServer: acrLoginServer
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    keyVaultName: keyVault.outputs.keyVaultName
    databaseHost: database.outputs.serverFqdn
  }
}

module apim 'modules/apim.bicep' = {
  name: '${prefix}-apim'
  params: {
    prefix: prefix
    location: location
    tags: tags
    environment: environment
    backendUrl: containerApps.outputs.backendUrl
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
  }
}

// ── Outputs ──────────────────────────────────────────────────────────
output apimGatewayUrl string = apim.outputs.gatewayUrl
output frontendUrl string = containerApps.outputs.frontendUrl
output backendUrl string = containerApps.outputs.backendUrl
output keyVaultName string = keyVault.outputs.keyVaultName
