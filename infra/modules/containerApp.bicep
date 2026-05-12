param name string
param location string
param environmentId string
param acrLoginServer string
param acrName string
param storageAccountName string
param keyVaultName string
param appInsightsConnectionString string
param imageTag string = 'latest'

resource ca 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 5000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: acrLoginServer, identity: 'system' }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: 'system'
        }
        {
          name: 'football-api-key'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/football-api-key'
          identity: 'system'
        }
        {
          // Shared secret for admin endpoints (/api/admin/reload-model, /api/fixtures/refresh).
          // Backend rejects requests without a matching X-Reload-Token header. The Key Vault
          // secret must be created out-of-band — Bicep only references it.
          name: 'reload-token'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/reload-token'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: '${acrLoginServer}/fotballpred-backend:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1.0Gi' }
          env: [
            { name: 'USE_BLOB_STORAGE', value: 'true' }
            { name: 'AZURE_STORAGE_ACCOUNT', value: storageAccountName }
            { name: 'FLASK_ENV', value: 'production' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'FOOTBALL_API_KEY', secretRef: 'football-api-key' }
            { name: 'RELOAD_TOKEN', secretRef: 'reload-token' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 5000 }
              initialDelaySeconds: 60
              periodSeconds: 30
              timeoutSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/api/health', port: 5000 }
              initialDelaySeconds: 30
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

resource acrResource 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}
resource storageResource 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource kvResource 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// AcrPull
resource roleAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acrResource
  name: guid(acrResource.id, ca.id, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: ca.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Blob Data Reader
resource roleBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageResource
  name: guid(storageResource.id, ca.id, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: ca.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User
resource roleKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kvResource
  name: guid(kvResource.id, ca.id, '4633458b-17de-408a-b874-0445c86b69e6')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: ca.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output fqdn string = ca.properties.configuration.ingress.fqdn
output principalId string = ca.identity.principalId
