# Infrastructure as Code (Bicep)

Provisioner hele Azure-stacken for Football Match Predictor.

## Struktur

- `main.bicep` — entry point, komponerer alle modulene
- `main.parameters.prod.json` — prod-parametere
- `modules/` — én fil per Azure-tjeneste

## Deploy lokalt

```powershell
$RG = "rg-fotballpred-prod"

# Validate
az deployment group validate `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.prod.json `
  --parameters postgresAdminPassword='<din-secret>' footballApiKey='<din-key>'

# Preview
az deployment group what-if `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.prod.json `
  --parameters postgresAdminPassword='<din-secret>' footballApiKey='<din-key>'

# Deploy
az deployment group create `
  --resource-group $RG `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.prod.json `
  --parameters postgresAdminPassword='<din-secret>' footballApiKey='<din-key>'
```

## Deploy via GitHub Actions

Push til main eller manuell trigger via Actions-tab → "Infrastructure (Bicep)" → "Run workflow".

## Tabula rasa-test

Den ultimate IaC-testen — slett alt og bygg fra Bicep:

```powershell
az group delete --name $RG --yes
az group create --name $RG --location swedencentral
az deployment group create --resource-group $RG --template-file infra/main.bicep --parameters infra/main.parameters.prod.json --parameters postgresAdminPassword='<...>' footballApiKey='<...>'
```

NB: Du må re-laste opp modeller til blob etter slett — kjør `scripts/upload-models.ps1` (se runbook).

## Outputs

Etter vellykket deploy:
- `backendUrl` — Container Apps FQDN
- `acrLoginServer` — Container Registry login server
- `storageAccountName`, `keyVaultName`, `postgresFqdn` — for runbook-kommandoer
