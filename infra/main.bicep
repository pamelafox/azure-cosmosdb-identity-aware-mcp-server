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

@description('OpenTelemetry platform for monitoring: appinsights, logfire, or none')
@allowed([
  'appinsights'
  'logfire'
  'none'
])
param openTelemetryPlatform string = 'appinsights'

// Derived boolean for App Insights resource creation
var useAppInsights = openTelemetryPlatform == 'appinsights'

@description('Entra ID group ID for admin access to expense statistics')
param entraAdminGroupId string = ''

@secure()
@description('Logfire token used by the server as a secret')
param logfireToken string = ''

@description('Service Management Reference for the app registration')
param serviceManagementReference string = ''

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

// App Service Plan
module appServicePlan 'appserviceplan.bicep' = {
  name: 'serviceplan'
  scope: resourceGroup
  params: {
    name: '${prefix}-serviceplan'
    location: location
    tags: tags
    sku: {
      name: 'S1'
    }
    reserved: true
  }
}

// App settings shared between initial deployment and auth update
var serverAppSettings = {
  RUNNING_IN_PRODUCTION: 'true'
  AZURE_COSMOSDB_ACCOUNT: cosmosDb.outputs.name
  AZURE_COSMOSDB_DATABASE: cosmosDbDatabaseName
  AZURE_COSMOSDB_USER_CONTAINER: cosmosDbUserContainerName
  APPLICATIONINSIGHTS_CONNECTION_STRING: useAppInsights ? applicationInsights!.outputs.connectionString : ''
  OPENTELEMETRY_PLATFORM: openTelemetryPlatform
  ENTRA_ADMIN_GROUP_ID: entraAdminGroupId
  LOGFIRE_TOKEN: logfireToken
  SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
  ENABLE_ORYX_BUILD: 'true'
}

// App Service for MCP server
module web 'appservice.bicep' = {
  name: 'web'
  scope: resourceGroup
  params: {
    name: replace('${take(prefix, 19)}-server', '--', '-')
    location: location
    tags: tags
    appServicePlanId: appServicePlan.outputs.id
    appCommandLine: 'uvicorn auth_entra_mcp:app --host 0.0.0.0 --port 8000'
    appSettings: serverAppSettings
  }
}

// Entra app registration
var issuer = '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
module registration 'appregistration.bicep' = {
  name: 'reg'
  scope: resourceGroup
  params: {
    clientAppName: '${prefix}-entra-mcp-app'
    clientAppDisplayName: 'MCP Expense Server App'
    webAppEndpoint: web.outputs.uri
    webAppIdentityId: web.outputs.identityPrincipalId
    issuer: issuer
    serviceManagementReference: serviceManagementReference
  }
}

// Configure Easy Auth on App Service (passes all app settings to avoid list() circular dependency)
module appupdate 'appupdate.bicep' = {
  name: 'appupdate'
  scope: resourceGroup
  params: {
    appServiceName: web.outputs.name
    clientId: registration.outputs.clientAppId
    openIdIssuer: issuer
    prmScope: registration.outputs.userImpersonationScope
    appSettings: union(serverAppSettings, {
      OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID: web.outputs.identityClientId
    })
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
    principalId: web.outputs.identityPrincipalId
    roleDefinitionId: '/${subscription().id}/resourceGroups/${resourceGroup.name}/providers/Microsoft.DocumentDB/databaseAccounts/${cosmosDb.outputs.name}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = resourceGroup.name

output SERVICE_SERVER_NAME string = web.outputs.name
output SERVICE_SERVER_URI string = web.outputs.uri

output AZURE_COSMOSDB_ACCOUNT string = cosmosDb.outputs.name
output AZURE_COSMOSDB_ENDPOINT string = cosmosDb.outputs.endpoint
output AZURE_COSMOSDB_DATABASE string = cosmosDbDatabaseName
output AZURE_COSMOSDB_USER_CONTAINER string = cosmosDbUserContainerName

output APPLICATIONINSIGHTS_CONNECTION_STRING string = useAppInsights ? applicationInsights!.outputs.connectionString : ''

output MCP_SERVER_URL string = '${web.outputs.uri}/mcp'

output OPENTELEMETRY_PLATFORM string = openTelemetryPlatform
