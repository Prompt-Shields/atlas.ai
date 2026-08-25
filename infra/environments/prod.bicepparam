using '../main.bicep'

param environment = 'prod'
param location = 'eastus2'
param projectName = 'aigrc'
param imageTag = 'prod-latest'
param acrLoginServer = 'aigrcprod.azurecr.io'
