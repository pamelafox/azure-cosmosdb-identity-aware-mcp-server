param appServiceName string

@description('The client ID of the Microsoft Entra application.')
param clientId string

param openIdIssuer string

@description('The scope value for PRM (Protected Resource Metadata) for MCP server authorization.')
param prmScope string = ''

@description('All app settings to write (avoids list() circular dependency).')
param appSettings object = {}

// VS Code client ID for MCP pre-authorization
var vsCodeClientId = 'aebc6443-996d-45c2-90f0-388ff96faa56'

resource appService 'Microsoft.Web/sites@2022-03-01' existing = {
  name: appServiceName
}

// Write all app settings including PRM scope (passed explicitly to avoid circular dependency)
resource configAppSettings 'Microsoft.Web/sites/config@2022-03-01' = {
  parent: appService
  name: 'appsettings'
  properties: union(appSettings, {
    WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES: prmScope
  })
}

resource configAuth 'Microsoft.Web/sites/config@2022-03-01' = {
  parent: appService
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: clientId
          clientSecretSettingName: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID'
          openIdIssuer: openIdIssuer
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedApplications: [
              vsCodeClientId
            ]
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}
