@description('Container Apps Job for nightly/weekly model retraining.')
param name string
param location string
param environmentId string
param acrLoginServer string
param storageAccountName string
param keyVaultName string
param imageTag string

// Cron expression — default Sunday 02:00 UTC. Heaviest match results have
// settled by then, and traffic to the prod container is near zero so the
// hot-reload at the end is gentle. Override per-env if needed.
param cronExpression string = '0 2 * * 0'

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      // 'Schedule' = cron-triggered. 'Manual' would let us start runs via
      // az CLI but skip the schedule. Schedule is what we want here.
      triggerType: 'Schedule'
      replicaTimeout: 3600        // 1h max — retrain is typically 5-15min on 1 CPU
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          identity: 'system'
          server: acrLoginServer
        }
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
          // Used to trigger backend hot-reload after a successful promote.
          // Shared secret with /api/admin/reload-model on the running app.
          name: 'reload-token'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/reload-token'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'retrain'
          image: '${acrLoginServer}/fotballpred-backend:${imageTag}'
          // Override entrypoint to run the retrain script instead of starting
          // gunicorn. The backend image already has all the deps (joblib,
          // pandas, xgboost, sklearn) — no need for a separate image.
          command: ['python']
          args: ['jobs/retrain.py']
          // Retrain is CPU-bound (XGBoost training on 7k+ matches). 1 vCPU /
          // 2Gi keeps the job within the consumption-plan SKU limits and
          // typically finishes in 5-15min. Raise both if you ever hit OOM
          // from a much larger training set.
          resources: { cpu: json('1.0'), memory: '2.0Gi' }
          env: [
            { name: 'USE_BLOB_STORAGE', value: 'true' }
            { name: 'AZURE_STORAGE_ACCOUNT', value: storageAccountName }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'FOOTBALL_API_KEY', secretRef: 'football-api-key' }
            { name: 'RELOAD_TOKEN', secretRef: 'reload-token' }
            // Tuned defaults — see retrain.py for what each means. Adjust via
            // `az containerapp job update --set-env-vars KEY=VAL` without redeploy.
            { name: 'AUC_TOLERANCE', value: '0.02' }
            { name: 'HOLDOUT_DAYS', value: '90' }
            { name: 'MIN_HOLDOUT_SIZE', value: '50' }
          ]
        }
      ]
    }
  }
}

output name string = job.name
output principalId string = job.identity.principalId
