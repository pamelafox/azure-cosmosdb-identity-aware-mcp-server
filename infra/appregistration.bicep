extension microsoftGraphV1

@description('Specifies the name of cloud environment to run this deployment in.')
param cloudEnvironment string = environment().name

@description('Audience uris for public and national clouds')
param audiences object = {
  AzureCloud: {
    uri: 'api://AzureADTokenExchange'
  }
  AzureUSGovernment: {
    uri: 'api://AzureADTokenExchangeUSGov'
  }
  USNat: {
    uri: 'api://AzureADTokenExchangeUSNat'
  }
  USSec: {
    uri: 'api://AzureADTokenExchangeUSSec'
  }
  AzureChinaCloud: {
    uri: 'api://AzureADTokenExchangeChina'
  }
}

@description('Specifies the ID of the user-assigned managed identity.')
param webAppIdentityId string

@description('Specifies the unique name for the client application.')
param clientAppName string

@description('Specifies the display name for the client application')
param clientAppDisplayName string

param serviceManagementReference string = ''

param issuer string

param webAppEndpoint string

// VS Code client ID for MCP pre-authorization
var vsCodeClientId = 'aebc6443-996d-45c2-90f0-388ff96faa56'

// Get the MS Graph Service Principal
var msGraphAppId = '00000003-0000-0000-c000-000000000000'
resource msGraphSP 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: msGraphAppId
}

var graphScopes = msGraphSP.oauth2PermissionScopes
var clientAppScopes = ['User.Read', 'offline_access', 'openid', 'profile']

resource clientApp 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: clientAppName
  displayName: clientAppDisplayName
  signInAudience: 'AzureADMyOrg'
  groupMembershipClaims: 'SecurityGroup'
  identifierUris: [
    'api://${clientAppName}'
  ]
  serviceManagementReference: empty(serviceManagementReference) ? null : serviceManagementReference
  web: {
    redirectUris: [
      '${webAppEndpoint}/.auth/login/aad/callback'
    ]
    implicitGrantSettings: { enableIdTokenIssuance: true }
  }
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: [
      {
        adminConsentDescription: 'Allows access to the MCP server as the signed-in user.'
        adminConsentDisplayName: 'Access MCP Server'
        id: guid(clientAppName, 'user_impersonation')
        isEnabled: true
        type: 'User'
        userConsentDescription: 'Allow access to the MCP server on your behalf'
        userConsentDisplayName: 'Access MCP Server'
        value: 'user_impersonation'
      }
    ]
    preAuthorizedApplications: [
      {
        appId: vsCodeClientId
        delegatedPermissionIds: [
          guid(clientAppName, 'user_impersonation')
        ]
      }
    ]
  }
  requiredResourceAccess: [
    {
      resourceAppId: msGraphAppId
      resourceAccess: [
        for (scope, i) in clientAppScopes: {
          id: filter(graphScopes, graphScopes => graphScopes.value == scope)[0].id
          type: 'Scope'
        }
      ]
    }
  ]

  resource clientAppFic 'federatedIdentityCredentials@v1.0' = {
    name: '${clientApp.uniqueName}/miAsFic'
    audiences: [
      audiences[cloudEnvironment].uri
    ]
    issuer: issuer
    subject: webAppIdentityId
  }
}

resource clientSp 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: clientApp.appId
}

output clientAppId string = clientApp.appId
output clientSpId string = clientSp.id
output userImpersonationScope string = 'api://${clientApp.appId}/user_impersonation'
