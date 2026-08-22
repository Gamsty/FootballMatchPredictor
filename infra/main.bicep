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

@description('Commit sha the backend image was built from. Passed through to the container as GIT_SHA and reported at /api/health. Left empty when deploying the `latest` tag, where the sha is genuinely unknown.')
param backendGitSha string = ''

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

// Reference the storage account by its deterministic name (same string the
// storage module uses) rather than `storage.outputs.name` — Bicep requires
// listKeys() arguments to be resolvable at deployment start, and module
// outputs are deploy-time values. The implicit ordering still holds because
// `cae` depends on `storage.outputs.oddsCacheShareName` further down.
var storageAccountName = 'st${prefixNoDash}'
var storageAccountKey = listKeys(
  resourceId('Microsoft.Storage/storageAccounts', storageAccountName),
  '2023-05-01'
).keys[0].value

module cae 'modules/containerAppsEnv.bicep' = {
  name: 'cae'
  params: {
    name: 'cae-${prefix}'
    location: location
    workspaceId: logAnalytics.outputs.workspaceId
    workspaceKey: logAnalytics.outputs.workspaceKey
    oddsCacheStorageAccountName: storageAccountName
    oddsCacheStorageAccountKey: storageAccountKey
    oddsCacheShareName: storage.outputs.oddsCacheShareName
  }
}

module containerApp 'modules/containerApp.bicep' = {
  name: 'containerApp'
  params: {
    name: 'ca-${prefix}'
    location: location
    environmentId: cae.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    storageAccountName: storage.outputs.name
    keyVaultName: keyVault.outputs.name
    appInsightsConnectionString: appInsights.outputs.connectionString
    imageTag: backendImageTag
    gitSha: backendGitSha
    oddsCacheStorageName: cae.outputs.oddsCacheStorageName
  }
}

// Weekly model retraining — separate Container Apps Job so it can run for
// 5-15 minutes without holding a gunicorn worker. Cron'd Sunday 02:00 UTC by
// default; output PrincipalId so caller can assign Storage Blob Data Contributor
// + Key Vault Secrets User out-of-band (RBAC is managed outside this template).
module retrainJob 'modules/retrainJob.bicep' = {
  name: 'retrainJob'
  params: {
    name: 'caj-retrain-${prefix}'
    location: location
    environmentId: cae.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    storageAccountName: storage.outputs.name
    keyVaultName: keyVault.outputs.name
    imageTag: backendImageTag
  }
}

output backendUrl string = containerApp.outputs.fqdn
output acrLoginServer string = acr.outputs.loginServer
output storageAccountName string = storage.outputs.name
output keyVaultName string = keyVault.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
// Surfaced so the post-deploy RBAC script can grant Storage Blob Data Contributor
// + Key Vault Secrets User to the job's identity (see infra/README.md).
output retrainJobPrincipalId string = retrainJob.outputs.principalId
output retrainJobName string = retrainJob.outputs.name
