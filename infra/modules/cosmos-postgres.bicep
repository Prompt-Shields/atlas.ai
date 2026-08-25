// ============================================================================
// Cosmos DB for PostgreSQL (Citus) with pgvector
// ============================================================================

param prefix string
param location string
param tags object
param environment string

var skuTier = environment == 'prod' ? 'GeneralPurpose' : 'Burstable'
var storageSizeGb = environment == 'prod' ? 256 : 32
var nodeCount = environment == 'prod' ? 2 : 1

resource cluster 'Microsoft.DBforPostgreSQL/serverGroupsv2@2022-11-08' = {
  name: '${prefix}-pg'
  location: location
  tags: tags
  properties: {
    postgresqlVersion: '16'
    citusVersion: '12.1'
    enableShardsOnCoordinator: true
    nodeCount: nodeCount
    coordinatorVCores: environment == 'prod' ? 4 : 2
    coordinatorStorageQuotaInMb: storageSizeGb * 1024
    coordinatorEnablePublicIpAccess: false
    nodeVCores: environment == 'prod' ? 4 : 2
    nodeStorageQuotaInMb: storageSizeGb * 1024
    nodeEnablePublicIpAccess: false
    coordinatorServerEdition: skuTier
    nodeServerEdition: skuTier
    enableHa: environment == 'prod'
  }
}

// Enable pgvector extension
resource pgvectorConfig 'Microsoft.DBforPostgreSQL/serverGroupsv2/configurations@2022-11-08' = {
  name: 'citus.enable_extension_creation'
  parent: cluster
  properties: {
    value: 'on'
  }
}

output serverFqdn string = cluster.properties.serverNames[0].fullyQualifiedDomainName
output clusterName string = cluster.name
