param name string
param location string
@secure()
param adminPassword string
param adminUser string = 'fotballadmin'

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: name
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: adminUser
    administratorLoginPassword: adminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7 }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

// NB: pgBouncer is NOT available on Burstable tier (B1ms etc).
// We rely on SQLAlchemy's QueuePool (configured in backend/src/database.py) for connection pooling.
// If we ever upgrade to General Purpose, re-enable pgbouncer here and switch port back to 6432.

resource fwAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pg
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pg
  name: 'football_predictor'
}

output fqdn string = pg.properties.fullyQualifiedDomainName
// Port 5432 (direct). pgBouncer would have used 6432 but is unavailable on Burstable tier.
output connectionString string = 'postgresql://${adminUser}:${adminPassword}@${pg.properties.fullyQualifiedDomainName}:5432/football_predictor?sslmode=require'
