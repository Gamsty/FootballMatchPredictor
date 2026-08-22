param name string
param location string
param environmentId string
param acrLoginServer string
param storageAccountName string
param keyVaultName string
param appInsightsConnectionString string
param imageTag string = 'latest'

@description('Name of the CAE-scoped storage definition for the Odds API cache file share. When empty, no volume is mounted and the cache falls back to in-memory only.')
param oddsCacheStorageName string = ''

@description('Commit sha of the running image, surfaced at /api/health as `version`. The backend deploy workflow sets this alongside the image; an infra-only deploy pins the image to `latest`, where the sha genuinely is unknown, so the default is empty rather than stale.')
param gitSha string = ''

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
            // Point the file-backed odds cache at the mounted share when one
            // is available. Without a mount the OddsAPIClient still works
            // (in-memory only) — the file persistence just no-ops. The path
            // matches the default the client constructs, but setting it here
            // makes the intent visible in the manifest.
            { name: 'ODDS_API_CACHE_FILE', value: empty(oddsCacheStorageName) ? '' : '/app/data/odds_cache.json' }
            // Cross-replica model reload. A POST to /api/admin/reload-model only
            // reaches ONE gunicorn worker in ONE replica; the marker file is how
            // the others learn to re-read the model. With maxReplicas 2 it has to
            // live on the shared Azure Files mount to work at all — on a local
            // container filesystem each replica would only ever see its own
            // writes and the peer would keep serving the old model indefinitely.
            // Left unset without a mount so the backend falls back to its
            // per-container default rather than pointing at a path that isn't there.
            { name: 'RELOAD_MARKER_FILE', value: empty(oddsCacheStorageName) ? '' : '/app/data/model_reload.json' }
            // Build identity. See the gitSha param for why this can be empty.
            { name: 'GIT_SHA', value: gitSha }
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
              // Readiness gets the DB-probing endpoint, liveness above does not:
              // an unreachable database should pull the replica out of rotation,
              // not restart-loop the container. /api/health stays process-only so
              // a Burstable-tier Postgres blip can't kill a healthy app.
              type: 'Readiness'
              httpGet: { path: '/api/health/ready', port: 5000 }
              initialDelaySeconds: 30
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
          // Mount the Azure Files share at /app/data so the Odds API cache
          // (odds_cache.json) survives container restarts. The Dockerfile
          // pre-creates and chowns /app/data; the SMB mount replaces it at
          // runtime — file ownership inside the mount follows the storage
          // account's SMB defaults (root:root, mode 0755), but the cache
          // client writes via atomic tmp+rename which works regardless.
          volumeMounts: empty(oddsCacheStorageName) ? [] : [
            { volumeName: 'odds-cache', mountPath: '/app/data' }
          ]
        }
      ]
      volumes: empty(oddsCacheStorageName) ? [] : [
        {
          name: 'odds-cache'
          storageType: 'AzureFile'
          storageName: oddsCacheStorageName
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
// Storage Blob Data Reader is sufficient today — the runtime container only
// reads models from blob. If we ever move bet writes or other state to blob
// (currently lives in Postgres), upgrade to 'Storage Blob Data Contributor'
// (ba92f5b4-2d11-453d-a403-e96b0029c9fe) to allow writes. The Odds API file
// cache mounts via Azure Files using a storage account *key* (set on the
// CAE storages resource), so it does NOT depend on the managed-identity
// RBAC chain — SMB auth is key-based on Container Apps.
//
// Grant commands (run as Owner once):
//   $CA_OID=$(az containerapp show -g <RG> -n <CA> --query identity.principalId -o tsv)
//   az role assignment create --assignee $CA_OID --role "AcrPull" --scope <ACR_ID>
//   az role assignment create --assignee $CA_OID --role "Storage Blob Data Reader" --scope <SA_ID>
//   az role assignment create --assignee $CA_OID --role "Key Vault Secrets User" --scope <KV_ID>

output fqdn string = ca.properties.configuration.ingress.fqdn
output principalId string = ca.identity.principalId
