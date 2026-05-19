param name string
param location string
param workspaceId string
@secure()
param workspaceKey string

@description('Azure Storage account name backing the odds-cache file share. Optional — when empty, no env-level storage is created and the Container App falls back to in-memory caching only.')
param oddsCacheStorageAccountName string = ''

@description('Storage account key for the Azure Files mount. Container Apps file mounts authenticate via account key (managed-identity SMB auth on CAE storages is not yet GA).')
@secure()
param oddsCacheStorageAccountKey string = ''

@description('File share name inside the storage account.')
param oddsCacheShareName string = 'odds-cache'

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspaceId
        sharedKey: workspaceKey
      }
    }
  }
}

// CAE-scoped storage definition for the Odds API cache file share. Container
// Apps reference this by `storageName` when declaring a volume — the actual
// SMB mount is set up by the Container Apps runtime, not by the app container.
// Conditional on having a storage account name so the env can be deployed
// without it (e.g. dev environments where in-memory caching is acceptable).
resource oddsCacheStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = if (!empty(oddsCacheStorageAccountName)) {
  parent: cae
  name: 'odds-cache'
  properties: {
    azureFile: {
      accountName: oddsCacheStorageAccountName
      accountKey: oddsCacheStorageAccountKey
      shareName: oddsCacheShareName
      accessMode: 'ReadWrite'
    }
  }
}

output environmentId string = cae.id
output oddsCacheStorageName string = empty(oddsCacheStorageAccountName) ? '' : oddsCacheStorage.name
