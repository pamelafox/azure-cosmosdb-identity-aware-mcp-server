param name string
param location string = resourceGroup().location
param tags object = {}

param identityName string
param containerAppsEnvironmentName string
param containerRegistryName string
param serviceName string = 'server'
param exists bool

@description('Environment variables for the container')
param env array = []

@description('Secrets for the container')
param secrets array = []

resource serverIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

module app 'core/host/container-app-upsert.bicep' = {
  name: '${serviceName}-container-app-module'
  params: {
    name: name
    location: location
    tags: union(tags, { 'azd-service-name': serviceName })
    identityName: serverIdentity.name
    exists: exists
    containerAppsEnvironmentName: containerAppsEnvironmentName
    containerRegistryName: containerRegistryName
    ingressEnabled: true
    env: env
    secrets: secrets
    targetPort: 8000
    probes: [
      {
        type: 'Startup'
        httpGet: {
          path: '/health'
          port: 8000
        }
        initialDelaySeconds: 10
        periodSeconds: 3
        failureThreshold: 60
      }
      {
        type: 'Readiness'
        httpGet: {
          path: '/health'
          port: 8000
        }
        initialDelaySeconds: 5
        periodSeconds: 5
        failureThreshold: 3
      }
      {
        type: 'Liveness'
        httpGet: {
          path: '/health'
          port: 8000
        }
        periodSeconds: 10
        failureThreshold: 3
      }
    ]
  }
}

output identityPrincipalId string = serverIdentity.properties.principalId
output name string = app.outputs.name
output hostName string = app.outputs.hostName
output uri string = app.outputs.uri
output imageName string = app.outputs.imageName
