param name string
param location string = resourceGroup().location
param tags object = {}

param serviceName string = 'server'
param appServicePlanId string

param appSettings object = {}
param appCommandLine string = ''

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-id'
  location: location
}

resource appService 'Microsoft.Web/sites@2022-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${webIdentity.id}': {} }
  }
  properties: {
    serverFarmId: appServicePlanId
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.13'
      alwaysOn: true
      ftpsState: 'Disabled'
      appCommandLine: appCommandLine
    }
    clientAffinityEnabled: false
    httpsOnly: true
  }

  resource configAppSettings 'config' = {
    name: 'appsettings'
    properties: union(appSettings, {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      ENABLE_ORYX_BUILD: 'true'
      OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID: webIdentity.properties.clientId
    })
  }

  resource configLogs 'config' = {
    name: 'logs'
    properties: {
      applicationLogs: { fileSystem: { level: 'Verbose' } }
      detailedErrorMessages: { enabled: true }
      failedRequestsTracing: { enabled: true }
      httpLogs: { fileSystem: { enabled: true, retentionInDays: 1, retentionInMb: 35 } }
    }
    dependsOn: [
      configAppSettings
    ]
  }
}

output identityPrincipalId string = webIdentity.properties.principalId
output identityClientId string = webIdentity.properties.clientId
output uri string = 'https://${appService.properties.defaultHostName}'
output name string = appService.name
