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
$REPO = "Gamsty/FootballMatchPredictor"   # juster til ditt GitHub repo
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

---

## 4.1 Manuell RBAC for Container Apps (én gang per miljø)

Bicep-templaten har **ikke** med rolle-tildelingene for Container App-ens system-assigned
managed identity, fordi den deploying-identiteten (UAMI for GitHub OIDC) bare har
`Contributor`, ikke `Microsoft.Authorization/roleAssignments/write`. Disse må gis manuelt
av en bruker med Owner på resource-gruppen:

```powershell
$CA_OID = az containerapp show -g $RG -n $CA --query identity.principalId -o tsv
$ACR_ID = az acr show --name $ACR --query id -o tsv
$ST_ID  = az storage account show --name $ST --resource-group $RG --query id -o tsv
$KV_ID  = az keyvault show --name $KV --query id -o tsv

az role assignment create --assignee $CA_OID --role "AcrPull"                  --scope $ACR_ID
az role assignment create --assignee $CA_OID --role "Storage Blob Data Reader" --scope $ST_ID
az role assignment create --assignee $CA_OID --role "Key Vault Secrets User"   --scope $KV_ID
```

Uten dette steget vil Container App ikke kunne pulle imaget, lese modeller fra Blob,
eller hente secrets fra Key Vault. RBAC kan ta opptil 5 min å propagere — restart
revisjonen om backend fortsatt feiler etter rolle-tildeling.

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

## 6.1 Skjemamigrasjoner (kolonner + indekser)

`init_db()` kjorer ved oppstart av containeren og er idempotent. Den gjor tre ting:

1. `create_all` — lager tabeller som mangler.
2. ADD COLUMN — legger til kolonner som kom til etter at tabellen ble laget
   (listen `MIGRATIONS` i `backend/src/database.py`).
3. Indekssynk — lager indekser som er deklarert på modellene, men mangler i
   databasen, og dropper de som star oppfort som overflodige.

Steg 3 finnes fordi `create_all` bare lager indekser som del av CREATE TABLE. En
indeks som ble lagt til i `__table_args__` etter at tabellen alt fantes, havner
aldri i en eksisterende database — og ingenting feiler. Det var tilfellet for
`ix_matches_home_date_status` / `ix_matches_away_date_status` i produksjon.

Loggen ved oppstart viser hva som skjedde:

```
Migration: created index ix_matches_home_date_status on matches
Migration: dropped redundant index ix_matches_id on matches
```

### Feature-pipeline-versjon

`match_features.pipeline_version` stempler hvilken feature-definisjon som
produserte hver rad. Den inkrementelle rekalkuleringen sammenlignet før bare mot
`match.updated_at` — den ser at en kamp har fått resultat, men aldri at selve
pipelinen har endret seg. Rader bygget med sesongsluttabeller så derfor
permanent oppdaterte ut, og den ukentlige retrain-jobben ville trent videre på
dem.

Migrasjonen legger til kolonnen med NULL på eksisterende rader, som leses som
«ukjent — bygg på nytt». Første retrain etter deploy rekalkulerer altså hele
`match_features` én gang (~5 min på 40k kamper, godt innenfor jobbens
2-timers `replicaTimeout`). Kjøringene etterpå hopper over som normalt.

Endrer du betydningen av en feature senere: bump
`FEATURE_PIPELINE_VERSION` i `backend/src/feature_engineering.py`. Da rydder
neste kjøring opp av seg selv — ingen `force=True` å huske.

```powershell
# Sjekk fordelingen etter en retrain
psql $DATABASE_URL -c "SELECT pipeline_version, count(*) FROM match_features GROUP BY 1"
```

### Hvis unik-indeksen på standings feiler

`uq_standings_team_season_comp` er UNIQUE. Har databasen allerede duplikater,
logger oppstarten dette og fortsetter uten indeksen:

```
Migration: could not create index uq_standings_team_season_comp on standings
(IntegrityError: ...). If it is UNIQUE, de-duplicate first.
```

Rydd opp — behold nyeste rad per (team_id, season, competition) — og restart
container-appen slik at migrasjonen kjorer på nytt:

```sql
DELETE FROM standings s
USING standings dup
WHERE s.team_id = dup.team_id
  AND s.season = dup.season
  AND s.competition = dup.competition
  AND s.id < dup.id;
```

Merk: CREATE INDEX tar en skrivelås på tabellen. På vart datavolum (~40k
matches-rader) tar det under et sekund, men det skjer ved oppstart av hver
worker — ikke deploy midt i en kamphelg hvis du nettopp la til en stor indeks.

---

## 6.2 Engangsreparasjon: syntetiske lag-IDer

Gjelder databaser som ble fylt for `generate_team_id` ble deterministisk.

Lag som bare finnes i football-data.co.uk-CSVene far en syntetisk `api_id`. Den
ble tidligere utledet fra Pythons innebygde `hash()`, som randomiserer
streng-hashing per prosess. IDen var derfor ulik for hver kjoring, mens
`add_team` matcher pa nettopp `api_id` — sa hver ny lasting la inn en ny
Team-rad per klubb, og klubbens kamphistorikk ble splittet over duplikatene.
Feature engineering slar opp pa primaernokkelen, sa form, h2h og hviledager ble
regnet ut pa en brokdel av den faktiske historikken.

Generatoren bruker na crc32 (stabil pa tvers av prosesser). Ryddejobben:

```powershell
# 1. Se hva som ville skjedd — skriver ingenting
python jobs/remap_synthetic_team_ids.py

# 2. Slå sammen duplikatlag og skriv nye deterministiske api_id-er
python jobs/remap_synthetic_team_ids.py --apply

# 3. Valgfritt: slett duplikatkamper som sammenslåingen avdekker.
#    Kun kamper UTEN prediksjoner/spill/snapshots slettes; resten rapporteres.
python jobs/remap_synthetic_team_ids.py --apply --merge-matches
```

Jobben rorer ikke lag med ekte football-data.org-IDer, og den er idempotent —
andre kjoring rapporterer null endringer. Kjor den for neste
`python src/load_external_csv.py`, ellers legges det inn enda et sett duplikater.

Etter opprydding bor features regnes pa nytt, siden historikken na er samlet:

```powershell
python src/feature_engineering.py
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

# /api/health er liveness: svarer 200 selv uten DB/modell.
curl "https://$APP_URL/api/health"
# /api/health/ready er den som faktisk sier om appen kan betjene trafikk.
curl "https://$APP_URL/api/health/ready"
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

### 10.1 OIDC via User-Assigned Managed Identity (UAMI)

På UiO-tenant er `az ad sp create-for-rbac` ofte blokkert for studentkontoer. Vi bruker en
UAMI istedenfor en service principal — federated credentials kobler GitHub OIDC-token til UAMI.

```powershell
$UAMI = "uami-github-fotballpred"

# Opprett UAMI
az identity create --name $UAMI --resource-group $RG --location $LOC
$APP_ID = az identity show --name $UAMI --resource-group $RG --query clientId -o tsv
$UAMI_OID = az identity show --name $UAMI --resource-group $RG --query principalId -o tsv
$TENANT_ID = az account show --query tenantId -o tsv

# Federated credential for push til main
az identity federated-credential create `
  --name github-main `
  --identity-name $UAMI `
  --resource-group $RG `
  --issuer "https://token.actions.githubusercontent.com" `
  --subject "repo:${REPO}:ref:refs/heads/main" `
  --audiences "api://AzureADTokenExchange"

# Federated credential for pull requests
az identity federated-credential create `
  --name github-pull-request `
  --identity-name $UAMI `
  --resource-group $RG `
  --issuer "https://token.actions.githubusercontent.com" `
  --subject "repo:${REPO}:pull_request" `
  --audiences "api://AzureADTokenExchange"

# Federated credential for environment:production
# Kreves fordi infra.yml-jobben bruker `environment: production` for godkjenningskrav.
# GitHub setter da OIDC-tokenets `sub` til `...:environment:production`, ikke `...:ref:refs/heads/main`.
az identity federated-credential create `
  --name github-env-production `
  --identity-name $UAMI `
  --resource-group $RG `
  --issuer "https://token.actions.githubusercontent.com" `
  --subject "repo:${REPO}:environment:production" `
  --audiences "api://AzureADTokenExchange"

# Roller: Contributor for å kunne kjøre Bicep + push images
$ACR_ID = az acr show --name $ACR --query id -o tsv
az role assignment create --assignee $UAMI_OID --role "Contributor" --scope "/subscriptions/$SUB_ID/resourceGroups/$RG"
az role assignment create --assignee $UAMI_OID --role "AcrPush" --scope $ACR_ID

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
- Require status checks before merging → "test" (backend) og "build" (frontend)
- Require branches to be up to date

### 10.4 Test workflow

Merg en PR til `main` → workflow `Backend CI/CD` skal kjøre og deploye automatisk.
Frontend har sin egen workflow (`Frontend CI`) som kjører lint + `vite build`.
Den deployer **ikke** — Vercel gjør det fra git — men den stopper en ødelagt
frontend på PR-en i stedet for i produksjon.

### 10.5 Smoke test og automatisk rollback

Deploy-jobben sjekker `/api/health/ready`, ikke `/api/health`.

Det er ikke en detalj: `/api/health` er bevisst prosess-only og svarer
`200 {"status":"healthy"}` selv uten database og uten modell lastet. Den gamle
smoke-testen traff den, så et deploy som ikke kunne betjene en eneste request
ble rapportert som vellykket. `/api/health/ready` svarer 503 til både databasen
og modellen er brukbar.

Begge endepunkter returnerer nå også `version` — commit-sha-en imaget ble bygget
fra, satt som `GIT_SHA` ved deploy. Smoke-testen krever at den matcher commit-en
som trigget kjøringen. Uten det kunne testen bli besvart av den **forrige**
revisjonen mens den nye fortsatt startet opp, og passere på feil grunnlag.

Sjekk hva som faktisk kjører:

```powershell
$APP_URL = az containerapp show --name $CA --resource-group $RG --query properties.configuration.ingress.fqdn -o tsv
curl "https://$APP_URL/api/health/ready"
# -> {"status":"ready","database":"ok","model_loaded":true,"version":"<sha>"}
```

Feiler smoke-testen, ruller jobben tilbake til imaget som lå der før deploy
(`GIT_SHA` følger med tilbake, så `/api/health` ikke lyver om hva som kjører).
Det feilende imaget blir liggende i ACR tagget med sin commit-sha, så det kan
undersøkes:

```powershell
az containerapp show --name $CA --resource-group $RG --query "properties.template.containers[0].image" -o tsv
```

Merk: `infra.yml` deployer med `backendImageTag=latest` fra
`main.parameters.prod.json`. En infra-deploy setter altså imaget til `latest` og
nullstiller `GIT_SHA` (Bicep eier hele env-arrayet). Det er tilsiktet — for
`latest` vet vi genuint ikke hvilken sha som kjører, og en tom verdi er mer
ærlig enn en utdatert. Kjør backend-workflowen på nytt for å feste den igjen.

---

## 11. Modell-retrening med safe deploy (Fase 10)

### 11.1 Bygg retrain-imaget

```powershell
az acr build --registry $ACR --image fotballpred-retrain:v1 --image fotballpred-retrain:latest --file backend/jobs/Dockerfile .
```

### 11.2 Generer reload-token i Key Vault (én gang per miljø)

Backend bruker `RELOAD_TOKEN`-env-varen som delt secret for admin-endepunktene
(`/api/admin/reload-model` og `/api/fixtures/refresh`). Bicep refererer denne fra Key Vault,
så du må sette verdien manuelt før første deploy.

```powershell
$RELOAD_TOKEN = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
az keyvault secret set --vault-name $KV --name "reload-token" --value $RELOAD_TOKEN
Write-Host "Reload token: $RELOAD_TOKEN  (lagre denne — brukes av retrain-jobben for å hot-reload backend)"
```

Container App-templaten i `infra/modules/containerApp.bicep` plukker den opp automatisk —
ingen `az containerapp secret set` eller `--set-env-vars` nødvendig.

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

> ⚠️ `backend/src/model_training.py` må eksponere `train_production_model(db, holdout_days=...)`
> som returnerer en dict med nøklene `model_data`, `X_holdout`, `y_holdout`, `holdout_size`,
> `train_size`, `cutoff`. Denne funksjonen trener samme arkitektur som produksjon
> (stacked ensemble: XGBoost + RandomForest → LR meta-learner med TimeSeriesSplit CV),
> slik at validation-gaten sammenligner like modeller.

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

Du må gjenta de manuelle stegene som Bicep ikke dekker:
- Steg 4.1 (RBAC for Container Apps managed identity — krever Owner)
- Steg 5 (bygg backend-image, eller la GitHub Actions gjøre det)
- Steg 6 (re-restore database fra dump)
- Steg 7 (last opp modeller til Blob)
- Steg 11.2 (sett `reload-token` i Key Vault hvis det ikke finnes fra før)
- Steg 11.4 (gjenskape Container Apps Job — ikke i Bicep-modulene ennå)

Det er en god kandidat for et `scripts/bootstrap.ps1`-script som dokumenterer disse manuelle trinnene.

---

## Feilsøking

| Symptom | Sjekk |
|---|---|
| Container App svarer ikke (502) | `az containerapp logs show --name $CA --resource-group $RG --follow` |
| `DefaultAzureCredential` feiler i container | Verifiser `--system-assigned`, og at managed identity har RBAC på Storage/KV |
| KV secret-referanse feiler | RBAC kan ta opptil 5 min å propagere — vent og restart revision |
| Postgres connection refused | Sjekk firewall (`az postgres flexible-server firewall-rule list ...`). Burstable B1ms støtter ikke pgBouncer, så porten er alltid 5432. |
| Bicep what-if/deploy gir `ServerStoppedError` | Postgres må kjøre under deploy. `az postgres flexible-server start -g $RG -n $PSQL` og vent på `Ready` |
| Bicep feiler med `Authorization failed ... roleAssignments/write` | RBAC-tildelingene gjøres manuelt — se steg 4.1 nedenfor |
| GitHub Actions OIDC feiler med `AADSTS700213: No matching federated identity` | Subject må matche eksakt. Sjekk om jobben bruker `environment:` — da må subjectet være `repo:owner/repo:environment:<env-name>`, ikke `:ref:refs/heads/main` |
