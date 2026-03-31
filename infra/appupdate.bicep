param appServiceName string

@description('All app settings to write (avoids list() circular dependency).')
param appSettings object = {}

resource appService 'Microsoft.Web/sites@2022-03-01' existing = {
  name: appServiceName
}

// Write all app settings including Entra config (passed explicitly to avoid circular dependency)
resource configAppSettings 'Microsoft.Web/sites/config@2022-03-01' = {
  parent: appService
  name: 'appsettings'
  properties: appSettings
}
