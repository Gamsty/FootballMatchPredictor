param name string
param location string
param environmentId string
param acrLoginServer string
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
        {
          // Bet-write gate for POST/DELETE on /api/bets and /api/bets/combo.
          // Public visitors can READ the bet log; only requests carrying a
          // matching X-Bet-Token header can write. Kept separate from
          // reload-token because this one's value gets shipped to browsers
          // (via ?bet_token=X → localStorage). If the KV secret doesn't
          // exist, the backend logs a warning and allows unauthenticated
          // writes (backward compat for pre-auth deployments).
          name: 'bet-write-token'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/bet-write-token'
          identity: 'system'
        }
        {
          // The Odds API key — powers /api/value-bets. When this secret doesn't
          // exist in Key Vault the backend gracefully degrades (Value tab shows
          // "not configured"), so the deploy doesn't hard-fail without it. Create
          // the KV secret before/after deploy via:
          //   az keyvault secret set --vault-name <kv> --name odds-api-key --value <key>
          //
          // FRAGILITY NOTE: Bicep doesn't validate that the KV secret exists at
          // deploy time. If you DELETE the Key Vault secret later (e.g. during a
          // key rotation), the Container App secret stays referenced but resolves
          // to empty at runtime — the backend sees ODDS_API_KEY='' and silently
          // degrades. Symptom: /api/value-bets returns enabled=false in prod
          // even though Bicep looks healthy.
          //
          // Resolution checklist when value-bets goes dark:
          //   1. az keyvault secret list --vault-name <kv> | grep odds-api-key
          //   2. If missing/disabled: re-create with `az keyvault secret set`
          //   3. Restart the Container App revision to re-resolve secrets:
          //      az containerapp revision restart -g <rg> -n <ca> --revision <latest>
          name: 'odds-api-key'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/odds-api-key'
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
            { name: 'BET_WRITE_TOKEN', secretRef: 'bet-write-token' }
            { name: 'ODDS_API_KEY', secretRef: 'odds-api-key' }
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

// RBAC role assignments are managed out-of-band, not by this template.
// Why: the deploying identity (UAMI used for GitHub OIDC) only has Contributor on
// this resource group, not Microsoft.Authorization/roleAssignments/write, so Bicep
// can't create them. On the student subscription we can't grant the broader
// 'Role Based Access Control Administrator' permission without Owner rights.
//
// The Container App's system-assigned managed identity needs these roles, granted
// manually once after the first deploy:
//   - AcrPull on the ACR                 (7f951dda-4ed3-4680-a7ca-43fe172d538d)
//   - Storage Blob Data Reader on the SA (2a2b9908-6ea1-4ae2-8e65-a410df84e7d1)
//   - Key Vault Secrets User on the KV   (4633458b-17de-408a-b874-0445c86b69e6)
//
// Grant commands (run as Owner once):
//   $CA_OID=$(az containerapp show -g <RG> -n <CA> --query identity.principalId -o tsv)
//   az role assignment create --assignee $CA_OID --role "AcrPull" --scope <ACR_ID>
//   az role assignment create --assignee $CA_OID --role "Storage Blob Data Reader" --scope <SA_ID>
//   az role assignment create --assignee $CA_OID --role "Key Vault Secrets User" --scope <KV_ID>

output fqdn string = ca.properties.configuration.ingress.fqdn
output principalId string = ca.identity.principalId
