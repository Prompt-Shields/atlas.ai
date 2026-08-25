using '../main.bicep'

param environment = 'dev'
param location = 'eastus2'
param projectName = 'aigrc'
param imageTag = 'dev-latest'
param acrLoginServer = 'aigrcdev.azurecr.io'
