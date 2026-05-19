param name string
param location string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource modelsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'models'
  properties: { publicAccess: 'None' }
}

// File share mounted into the Container App at /app/data. Persists the Odds
// API cache across container restarts so the free-tier 500 req/month budget
// isn't burned on every cold start (minReplicas=0 means containers get evicted
// after idle, which would otherwise re-hydrate the cache from cold every wake).
// 1 GiB quota is far more than the cache needs (currently ~1 KB per league fetch);
// kept low because Azure Files pricing scales by provisioned size.
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource oddsCacheShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'odds-cache'
  properties: {
    shareQuota: 1
    enabledProtocols: 'SMB'
  }
}

output name string = storage.name
output id string = storage.id
output oddsCacheShareName string = oddsCacheShare.name
