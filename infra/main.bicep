targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name which is used to generate a short unique hash for each resource')
param name string

@minLength(1)
@description('Primary location for all resources')
param location string

@description('Id of the user or app to assign application roles')
param principalId string = ''

param serverExists bool = false

// App Insights is always enabled
var useAppInsights = true

@description('Entra ID group ID for admin access to expense statistics')
param entraAdminGroupId string = ''

@description('Entra ID app registration client ID for local dev OAuth')
param entraDevClientId string = ''

@secure()
@description('Entra ID app registration client secret for local dev OAuth')
param entraDevClientSecret string = ''

@description('Entra ID production app registration client ID (created by auth_init.py)')
param entraProdClientId string = ''

var resourceToken = toLower(uniqueString(subscription().id, name, location))
var tags = { 'azd-env-name': name }

resource resourceGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${name}-rg'
  location: location
  tags: tags
}

var prefix = '${name}-${resourceToken}'

// Cosmos DB configuration
var cosmosDbDatabaseName = 'expenses-database'
var cosmosDbUserContainerName = 'user-expenses'

// Cosmos DB for storing expenses
module cosmosDb 'br/public:avm/res/document-db/database-account:0.6.1' = {
  name: 'cosmosdb'
  scope: resourceGroup
  params: {
    name: '${resourceToken}-cosmos'
    location: location
    tags: tags
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    networkRestrictions: {
      ipRules: []
      publicNetworkAccess: 'Enabled'
      virtualNetworkRules: []
    }
    sqlDatabases: [
      {
        name: cosmosDbDatabaseName
        containers: [
          {
            name: cosmosDbUserContainerName
            kind: 'Hash'
            paths: [
              '/user_id'
            ]
          }
        ]
      }
    ]
  }
}

module logAnalyticsWorkspace 'br/public:avm/res/operational-insights/workspace:0.7.0' = if (useAppInsights) {
  name: 'loganalytics'
  scope: resourceGroup
  params: {
    name: '${prefix}-loganalytics'
    location: location
    tags: tags
    skuName: 'PerGB2018'
    dataRetention: 30
    useResourcePermissions: true
  }
}

// Application Insights for telemetry
module applicationInsights 'br/public:avm/res/insights/component:0.4.2' = if (useAppInsights) {
  name: 'applicationinsights'
  scope: resourceGroup
  params: {
    name: '${prefix}-appinsights'
    location: location
    tags: tags
    workspaceResourceId: logAnalyticsWorkspace.?outputs.resourceId!
    kind: 'web'
    applicationType: 'web'
  }
}

// Portal dashboard with Log Analytics queries visualizing MCP tools metrics
module applicationInsightsDashboard 'appinsights-dashboard.bicep' = if (useAppInsights) {
  name: 'application-insights-dashboard'
  scope: resourceGroup
  params: {
    name: '${prefix}-dashboard'
    location: location
    applicationInsightsName: applicationInsights!.outputs.name
  }
}

// Container apps host (including container registry)
module containerApps 'core/host/container-apps.bicep' = {
  name: 'container-apps'
  scope: resourceGroup
  params: {
    name: 'app'
    location: location
    tags: tags
    containerAppsEnvironmentName: '${prefix}-containerapps-env'
    containerRegistryName: '${take(replace(prefix, '-', ''), 42)}registry'
    logAnalyticsWorkspaceName: useAppInsights ? logAnalyticsWorkspace!.outputs.name : ''
    usePrivateIngress: false
  }
}

// Container app for MCP server
var containerAppDomain = replace('${take(prefix,15)}-server', '--', '-')
var mcpServerBaseUrl = 'https://${containerAppDomain}.${containerApps.outputs.defaultDomain}'
var serverIdentityName = '${prefix}-id-server'

// Managed identity for the server container app
module serverIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.0' = {
  name: 'server-identity'
  scope: resourceGroup
  params: {
    name: serverIdentityName
    location: location
    tags: tags
  }
}

// Shared environment variables for the server container app
var serverEnv = [
  { name: 'RUNNING_IN_PRODUCTION', value: 'true' }
  { name: 'AZURE_CLIENT_ID', value: serverIdentity.outputs.clientId }
  { name: 'AZURE_COSMOSDB_ACCOUNT', value: cosmosDb.outputs.name }
  { name: 'AZURE_COSMOSDB_DATABASE', value: cosmosDbDatabaseName }
  { name: 'AZURE_COSMOSDB_USER_CONTAINER', value: cosmosDbUserContainerName }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: useAppInsights ? applicationInsights!.outputs.connectionString : '' }
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'MCP_SERVER_BASE_URL', value: mcpServerBaseUrl }
  { name: 'ENTRA_ADMIN_GROUP_ID', value: entraAdminGroupId }
  { name: 'ENTRA_PROD_CLIENT_ID', value: entraProdClientId }
]

var entraDevEnv = !empty(entraDevClientId) ? [
  { name: 'ENTRA_DEV_CLIENT_ID', value: entraDevClientId }
  { name: 'ENTRA_DEV_CLIENT_SECRET', secretRef: 'entra-dev-client-secret' }
] : []

var entraDevSecrets = !empty(entraDevClientSecret) ? [
  { name: 'entra-dev-client-secret', value: entraDevClientSecret }
] : []

module server 'server.bicep' = {
  name: 'server'
  scope: resourceGroup
  params: {
    name: containerAppDomain
    location: location
    tags: tags
    identityName: serverIdentityName
    containerAppsEnvironmentName: containerApps.outputs.environmentName
    containerRegistryName: containerApps.outputs.registryName
    exists: serverExists
    env: concat(serverEnv, entraDevEnv)
    secrets: entraDevSecrets
  }
}

// Cosmos DB Data Contributor role for user
module cosmosDbRoleUser 'core/security/documentdb-sql-role.bicep' = {
  scope: resourceGroup
  name: 'cosmosdb-role-user'
  params: {
    databaseAccountName: cosmosDb.outputs.name
    principalId: principalId
    roleDefinitionId: '/${subscription().id}/resourceGroups/${resourceGroup.name}/providers/Microsoft.DocumentDB/databaseAccounts/${cosmosDb.outputs.name}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
  }
}

// Cosmos DB Data Contributor role for server
module cosmosDbRoleServer 'core/security/documentdb-sql-role.bicep' = {
  scope: resourceGroup
  name: 'cosmosdb-role-server'
  params: {
    databaseAccountName: cosmosDb.outputs.name
    principalId: server.outputs.identityPrincipalId
    roleDefinitionId: '/${subscription().id}/resourceGroups/${resourceGroup.name}/providers/Microsoft.DocumentDB/databaseAccounts/${cosmosDb.outputs.name}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = resourceGroup.name

output SERVICE_SERVER_IDENTITY_PRINCIPAL_ID string = server.outputs.identityPrincipalId
output SERVICE_SERVER_NAME string = server.outputs.name
output SERVICE_SERVER_URI string = server.outputs.uri
output SERVICE_SERVER_IMAGE_NAME string = server.outputs.imageName

output AZURE_CONTAINER_ENVIRONMENT_NAME string = containerApps.outputs.environmentName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerApps.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = containerApps.outputs.registryName

output AZURE_COSMOSDB_ACCOUNT string = cosmosDb.outputs.name
output AZURE_COSMOSDB_ENDPOINT string = cosmosDb.outputs.endpoint
output AZURE_COSMOSDB_DATABASE string = cosmosDbDatabaseName
output AZURE_COSMOSDB_USER_CONTAINER string = cosmosDbUserContainerName

output APPLICATIONINSIGHTS_CONNECTION_STRING string = useAppInsights ? applicationInsights!.outputs.connectionString : ''

output MCP_SERVER_URL string = '${server.outputs.uri}/mcp'

output MCP_SERVER_BASE_URL string = mcpServerBaseUrl
