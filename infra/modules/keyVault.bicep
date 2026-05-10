param name string
param location string
param secrets array = []

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource kvSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in secrets: {
  parent: kv
  name: s.name
  properties: {
    value: s.value
  }
}]

output name string = kv.name
output id string = kv.id
output uri string = kv.properties.vaultUri
