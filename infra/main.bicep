targetScope = 'resourceGroup'

@description('Project name (used as base for resource names)')
param projectName string = 'fotballpred'

@allowed(['prod', 'dev'])
param env string = 'prod'

@description('Azure region')
param location string = resourceGroup().location

@secure()
@description('PostgreSQL admin password')
param postgresAdminPassword string

@secure()
@description('football-data.org API key')
param footballApiKey string

@description('Image tag for the backend container')
param backendImageTag string = 'latest'

var prefix = '${projectName}-${env}'
var prefixNoDash = '${projectName}${env}'

module logAnalytics 'modules/logAnalytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: 'log-${prefix}'
    location: location
  }
}

module appInsights 'modules/appInsights.bicep' = {
  name: 'appInsights'
  params: {
    name: 'ai-${prefix}'
    location: location
    workspaceId: logAnalytics.outputs.id
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: 'acr${prefixNoDash}'
    location: location
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    name: 'st${prefixNoDash}'
    location: location
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    name: 'psql-${prefix}'
    location: location
    adminPassword: postgresAdminPassword
  }
}

module keyVault 'modules/keyVault.bicep' = {
  name: 'keyVault'
  params: {
    name: 'kv-${prefix}'
    location: location
    secrets: [
      { name: 'database-url', value: postgres.outputs.connectionString }
      { name: 'football-api-key', value: footballApiKey }
    ]
  }
}

module cae 'modules/containerAppsEnv.bicep' = {
  name: 'cae'
  params: {
    name: 'cae-${prefix}'
    location: location
    workspaceId: logAnalytics.outputs.workspaceId
    workspaceKey: logAnalytics.outputs.workspaceKey
  }
}

module containerApp 'modules/containerApp.bicep' = {
  name: 'containerApp'
  params: {
    name: 'ca-${prefix}'
    location: location
    environmentId: cae.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    acrName: acr.outputs.name
    storageAccountName: storage.outputs.name
    keyVaultName: keyVault.outputs.name
    appInsightsConnectionString: appInsights.outputs.connectionString
    imageTag: backendImageTag
  }
}

output backendUrl string = containerApp.outputs.fqdn
output acrLoginServer string = acr.outputs.loginServer
output storageAccountName string = storage.outputs.name
output keyVaultName string = keyVault.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
