param name string
param location string

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

output workspaceId string = law.properties.customerId
output workspaceKey string = law.listKeys().primarySharedKey
output id string = law.id
