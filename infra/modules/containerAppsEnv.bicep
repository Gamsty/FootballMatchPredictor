param name string
param location string
param workspaceId string
@secure()
param workspaceKey string

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

output environmentId string = cae.id
