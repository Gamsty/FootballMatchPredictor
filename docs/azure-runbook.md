# Azure deployment runbook

Kjør stegene i denne filen for å provisjonere og deploye til Azure. **Alle kommandoer som krever `az login`/Azure-konto må kjøres av deg lokalt** — koden i repoet er klar.

> **PowerShell-tips:** Hold den samme terminalen åpen gjennom hele runbooken. Variablene (`$RG`, `$ACR`, etc.) lever kun i den økten.

---

## 0. Forutsetninger

```powershell
# Verifiser
docker --version
az --version
az bicep version
pg_dump --version
psql --version
```

Hvis noen mangler, se [Hva mer trenger jeg å installere?](../README.md) eller installer:
- Docker Desktop: https://www.docker.com/products/docker-desktop/
- `winget install -e --id Microsoft.AzureCLI`
- `winget install -e --id PostgreSQL.PostgreSQL.16`

```powershell
az login
az account set --subscription "Azure for Students"
az account show  # verifiser
```

---

## 1. Sett base-variabler (én gang per terminal-økt)

```powershell
$RG = "rg-fotballpred-prod"
$LOC = "swedencentral"
$ACR = "acrfotballpredprod"
$ST = "stfotballpredprod"
$KV = "kv-fotballpred-prod"
$PSQL = "psql-fotballpred-prod"
$CAE = "cae-fotballpred-prod"
$CA = "ca-fotballpred-prod"
$JOB = "caj-fotballpred-retrain"
$LAW = "log-fotballpred-prod"
$AI_NAME = "ai-fotballpred-prod"
$DBADMIN = "fotballadmin"
$DBPASS = "<GENERER-STERK-PASSORD>"   # f.eks. fra 1Password
$EMAIL = "gamsten502@gmail.com"
$REPO = "adriakg/FootballMatchPredictor"  # juster
$SUB_ID = az account show --query id -o tsv
```

---

## 2. Test lokalt med Docker (Fase 1)

```powershell
docker compose up --build
# I et nytt vindu:
curl http://localhost:5000/api/health
# Frontend: http://localhost:8080
```

Stopp med Ctrl+C i compose-vinduet. Hvis dette fungerer er du klar for Azure.

---

## 3. Resource group + budget alert (Fase 2)

```powershell
az group create --name $RG --location $LOC

# Budget alert
$budgetJson = @"
{
  "properties": {
    "category": "Cost",
    "amount": 20,
    "timeGrain": "Monthly",
    "timePeriod": { "startDate": "$(Get-Date -Format 'yyyy-MM-01')T00:00:00Z" },
    "notifications": {
      "actual_GreaterThan_80_Percent": {
        "enabled": true,
        "operator": "GreaterThan",
        "threshold": 80,
        "contactEmails": ["$EMAIL"],
        "thresholdType": "Actual"
      }
    }
  }
}
"@
$budgetJson | Out-File -Encoding utf8 budget.json
az rest --method put `
  --uri "https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$RG/providers/Microsoft.Consumption/budgets/budget-fotballpred-monthly?api-version=2023-05-01" `
  --body "@budget.json"
Remove-Item budget.json
```

---

## 4. Provisjonér ALT via Bicep (Fase 8 — anbefalt)

Bicep-koden er ferdig i `infra/`. Den oppretter ACR, Storage, Key Vault, Postgres, Log Analytics, App Insights, Container Apps Environment **og** Container App med managed identity + RBAC i ett.

```powershell
az deployment group create `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.prod.json `
  --parameters postgresAdminPassword=$DBPASS `
               footballApiKey="<din-football-data-org-key>"
```

**Første deploy feiler** — Container App-ressursen prøver å pulle `fotballpred-backend:latest` fra ACR som ikke finnes ennå. Det er forventet. Fortsett til steg 5 for å bygge imaget, så kjør Bicep-deploy på nytt.

> **Alternativ:** Hvis du vil gjøre stegene manuelt for å lære dem (uten Bicep), følg fasene 2–5 i `azure-implementation-guide.md`. Bicep-veien anbefales — den lar deg slette og bygge opp igjen alt med én kommando.

---

## 5. Bygg + push backend-imaget

```powershell
az acr build --registry $ACR --image fotballpred-backend:v1 --image fotballpred-backend:latest ./backend
```

Kjør Bicep-deploy igjen — denne gangen finner Container App imaget:

```powershell
az deployment group create `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.prod.json `
  --parameters postgresAdminPassword=$DBPASS `
               footballApiKey="<din-football-data-org-key>"
```

---

## 6. Migrer database fra lokal Postgres (Fase 3)

```powershell
# Tillat din IP på Azure Postgres
$MYIP = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing).Content
az postgres flexible-server firewall-rule create `
  --resource-group $RG `
  --name $PSQL `
  --rule-name "AllowMyIP" `
  --start-ip-address $MYIP `
  --end-ip-address $MYIP

# Dump fra lokal — bruk port 5432 (direkte) for dump/restore, IKKE 6432 (pgBouncer)
pg_dump -h localhost -U postgres -d football_predictor -F c -f football_predictor.dump

# Restore til Azure (port 5432 for restore)
$AZ_HOST = "$PSQL.postgres.database.azure.com"
pg_restore -h $AZ_HOST -U $DBADMIN -d football_predictor --no-owner --no-acl football_predictor.dump
# Skriv inn $DBPASS når den prompter

Remove-Item football_predictor.dump
```

---

## 7. Last opp modeller til Blob Storage (Fase 4)

```powershell
$ST_KEY = az storage account keys list --resource-group $RG --account-name $ST --query "[0].value" -o tsv

az storage blob upload `
  --account-name $ST --account-key $ST_KEY `
  --container-name "models" `
  --name "production/best_model.pkl" `
  --file "backend/models/best_model.pkl"

az storage blob upload `
  --account-name $ST --account-key $ST_KEY `
  --container-name "models" `
  --name "production/multi_market_models.pkl" `
  --file "backend/models/multi_market_models.pkl"
```

---

## 8. Test backend i Azure

```powershell
$APP_URL = az containerapp show --name $CA --resource-group $RG --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Backend URL: https://$APP_URL"

curl "https://$APP_URL/api/health"
```

Hvis det feiler, sjekk loggene:

```powershell
az containerapp logs show --name $CA --resource-group $RG --follow
```

Vanligste feil: managed identity RBAC ikke propagert ennå — vent 1–2 min, restart revision:
```powershell
az containerapp revision restart --name $CA --resource-group $RG --revision (az containerapp revision list --name $CA --resource-group $RG --query "[0].name" -o tsv)
```

---

## 9. Koble Vercel-frontend til ny backend (Fase 6)

1. vercel.com → Project → Settings → Environment Variables
2. Sett `VITE_API_URL = https://<APP_URL>` (verdien fra steg 8) for Production + Preview + Development
3. Trigger en ny Vercel-deploy:
   ```powershell
   git commit --allow-empty -m "Trigger Vercel rebuild with new VITE_API_URL"
   git push
   ```
4. Smoke-test: åpne Vercel-URL, kjør en prediksjon, sjekk Network-tab i DevTools

---

## 10. CI/CD med GitHub Actions (Fase 7)

### 10.1 OIDC service principal

```powershell
$APP_NAME = "github-actions-fotballpred"
$SP = az ad sp create-for-rbac --name $APP_NAME --role contributor `
  --scopes "/subscriptions/$SUB_ID/resourceGroups/$RG" `
  --json-auth | ConvertFrom-Json

$APP_ID = $SP.clientId
$TENANT_ID = $SP.tenantId
$APP_OBJECT_ID = az ad app show --id $APP_ID --query id -o tsv

$fedCred = @{
  name = "github-main"
  issuer = "https://token.actions.githubusercontent.com"
  subject = "repo:${REPO}:ref:refs/heads/main"
  audiences = @("api://AzureADTokenExchange")
} | ConvertTo-Json
$fedCred | Out-File -Encoding utf8 fedcred.json
az ad app federated-credential create --id $APP_OBJECT_ID --parameters "@fedcred.json"
Remove-Item fedcred.json

# AcrPush rolle for å kunne pushe images
$ACR_ID = az acr show --name $ACR --query id -o tsv
az role assignment create --assignee $APP_ID --role "AcrPush" --scope $ACR_ID

Write-Host "AZURE_CLIENT_ID: $APP_ID"
Write-Host "AZURE_TENANT_ID: $TENANT_ID"
Write-Host "AZURE_SUBSCRIPTION_ID: $SUB_ID"
```

### 10.2 Sett GitHub repo secrets

GitHub repo → Settings → Secrets and variables → Actions → New repository secret:

| Navn | Verdi |
|---|---|
| `AZURE_CLIENT_ID` | (output fra forrige steg) |
| `AZURE_TENANT_ID` | (output fra forrige steg) |
| `AZURE_SUBSCRIPTION_ID` | (output fra forrige steg) |
| `AZURE_RG` | `rg-fotballpred-prod` |
| `AZURE_ACR_NAME` | `acrfotballpredprod` |
| `AZURE_CA_NAME` | `ca-fotballpred-prod` |
| `POSTGRES_ADMIN_PASSWORD` | (din $DBPASS) |
| `FOOTBALL_API_KEY` | (din football-data.org key) |

### 10.3 Branch protection

GitHub → Settings → Branches → Add rule for `main`:
- Require status checks before merging → "test"
- Require branches to be up to date

### 10.4 Test workflow

Merg en PR til `main` → workflow `Backend CI/CD` skal kjøre og deploye automatisk.

---

## 11. Modell-retrening med safe deploy (Fase 10)

### 11.1 Bygg retrain-imaget

```powershell
az acr build --registry $ACR --image fotballpred-retrain:v1 --image fotballpred-retrain:latest --file backend/jobs/Dockerfile .
```

### 11.2 Generer reload-token

```powershell
$RELOAD_TOKEN = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
az keyvault secret set --vault-name $KV --name "reload-token" --value $RELOAD_TOKEN

# Sett på Container App også
$RELOAD_URI = az keyvault secret show --vault-name $KV --name "reload-token" --query id -o tsv
az containerapp secret set --name $CA --resource-group $RG `
  --secrets "reload-token=keyvaultref:$RELOAD_URI,identityref:system"
az containerapp update --name $CA --resource-group $RG `
  --set-env-vars "RELOAD_TOKEN=secretref:reload-token"
```

### 11.3 Hent KV-secret URIs for jobben

```powershell
$DB_URL_URI = az keyvault secret show --vault-name $KV --name "database-url" --query id -o tsv
$API_KEY_URI = az keyvault secret show --vault-name $KV --name "football-api-key" --query id -o tsv
$RELOAD_URI = az keyvault secret show --vault-name $KV --name "reload-token" --query id -o tsv

$ACR_SERVER = az acr show --name $ACR --query loginServer -o tsv
```

### 11.4 Opprett Container Apps Job

```powershell
az containerapp job create `
  --name $JOB `
  --resource-group $RG `
  --environment $CAE `
  --image "$ACR_SERVER/fotballpred-retrain:v1" `
  --trigger-type Schedule `
  --cron-expression "0 3 * * *" `
  --replica-timeout 3600 `
  --replica-retry-limit 1 `
  --parallelism 1 `
  --replica-completion-count 1 `
  --cpu 1.0 `
  --memory 2.0Gi `
  --mi-system-assigned `
  --registry-server $ACR_SERVER `
  --registry-identity system `
  --secrets `
    "database-url=keyvaultref:$DB_URL_URI,identityref:system" `
    "football-api-key=keyvaultref:$API_KEY_URI,identityref:system" `
    "reload-token=keyvaultref:$RELOAD_URI,identityref:system" `
  --env-vars `
    USE_BLOB_STORAGE=true `
    AZURE_STORAGE_ACCOUNT=$ST `
    "DATABASE_URL=secretref:database-url" `
    "FOOTBALL_API_KEY=secretref:football-api-key" `
    "RELOAD_TOKEN=secretref:reload-token" `
    "BACKEND_RELOAD_URL=https://$APP_URL/api/admin/reload-model"
```

### 11.5 RBAC for jobben

```powershell
$JOB_PRINCIPAL = az containerapp job identity show --name $JOB --resource-group $RG --query principalId -o tsv
$ST_ID = az storage account show --name $ST --resource-group $RG --query id -o tsv
$KV_ID = az keyvault show --name $KV --query id -o tsv

az role assignment create --assignee $JOB_PRINCIPAL --role "Storage Blob Data Contributor" --scope $ST_ID
az role assignment create --assignee $JOB_PRINCIPAL --role "Key Vault Secrets User" --scope $KV_ID
az role assignment create --assignee $JOB_PRINCIPAL --role "AcrPull" --scope $ACR_ID
```

### 11.6 Kjør jobben manuelt for å teste

```powershell
az containerapp job start --name $JOB --resource-group $RG
az containerapp job execution list --name $JOB --resource-group $RG -o table
```

> ⚠️ `backend/src/model_training.py` må eksponere `train_xgboost(db)` som returnerer `model_data`-dict-en med samme shape som `best_model.pkl`. Hvis ikke kommer jobben til å feile med "model_training.py does not expose train_xgboost(db)". Refaktor i så fall.

---

## 12. Observability — alert (Fase 9)

```powershell
$AI_ID = az monitor app-insights component show --app $AI_NAME --resource-group $RG --query id -o tsv

az monitor action-group create `
  --name "ag-fotballpred-email" `
  --resource-group $RG `
  --short-name "fbpred" `
  --email-receivers name=admin email=$EMAIL

$AG_ID = az monitor action-group show --name "ag-fotballpred-email" --resource-group $RG --query id -o tsv

az monitor scheduled-query create `
  --name "alert-5xx-errors" `
  --resource-group $RG `
  --scopes $AI_ID `
  --condition "count 'union requests, exceptions | where resultCode startswith \"5\" or itemType == \"exception\"' > 5" `
  --window-size 5m `
  --evaluation-frequency 5m `
  --action-groups $AG_ID `
  --description "5xx errors above threshold"
```

App Insights traces ligger automatisk inn allerede — telemetry-koden i `backend/src/telemetry.py` aktiveres når `APPLICATIONINSIGHTS_CONNECTION_STRING` er satt (Bicep gjør det).

---

## 13. Spar penger når du ikke jobber

```powershell
# Skru av Postgres
az postgres flexible-server stop --resource-group $RG --name $PSQL

# Container Apps scaler til 0 automatisk — ingen kostnad ved idle
```

Start igjen senere:
```powershell
az postgres flexible-server start --resource-group $RG --name $PSQL
```

---

## 14. Riv ned alt (cleanup)

```powershell
az group delete --name $RG --yes
```

---

## Den ultimate IaC-testen

For å bevise at Bicep faktisk gjenskaper alt:

```powershell
az group delete --name $RG --yes
az group create --name $RG --location $LOC
az deployment group create --resource-group $RG --template-file infra/main.bicep --parameters infra/main.parameters.prod.json --parameters postgresAdminPassword=$DBPASS footballApiKey="<key>"
```

Du må gjenta:
- Steg 5 (bygg backend-image)
- Steg 6 (re-restore database)
- Steg 7 (last opp modeller)
- Steg 11 (gjenskape job-en)

Det er en god kandidat for et `scripts/bootstrap.ps1`-script som dokumenterer disse manuelle trinnene.

---

## Feilsøking

| Symptom | Sjekk |
|---|---|
| Container App svarer ikke (502) | `az containerapp logs show --name $CA --resource-group $RG --follow` |
| `DefaultAzureCredential` feiler i container | Verifiser `--system-assigned`, og at managed identity har RBAC på Storage/KV |
| KV secret-referanse feiler | RBAC kan ta opptil 5 min å propagere — vent og restart revision |
| Postgres connection refused | Sjekk firewall (`az postgres flexible-server firewall-rule list ...`), at du bruker port 6432 fra app, 5432 for admin-tasks |
| `pgbouncer.enabled` ble ignorert | Krever restart av Flexible Server: `az postgres flexible-server restart ...` |
| GitHub Actions OIDC feiler | Federated credential `subject` må matche eksakt: `repo:owner/repo:ref:refs/heads/main` |
