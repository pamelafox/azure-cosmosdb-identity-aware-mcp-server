@description('Name of the existing Container App to update')
param name string

param location string = resourceGroup().location
param tags object = {}
param serviceName string = 'server'

@description('Name of the user-assigned managed identity')
param identityName string

@description('Name of the Container Apps environment')
param containerAppsEnvironmentName string

@description('Name of the Container Registry')
param containerRegistryName string

@description('Production Entra app registration client ID (from appregistration.bicep)')
param entraAppClientId string

@description('The shared env array from main.bicep')
param env array

@description('The secrets array from main.bicep')
param secrets array = []

// Patch the container app to add the production ENTRA_PROD_CLIENT_ID env var.
// This runs after appregistration.bicep creates the app registration,
// breaking the circular dependency (server needs client ID, registration needs server URI).
module patchEnv 'core/host/container-app-upsert.bicep' = {
  name: '${name}-env-patch'
  params: {
    name: name
    location: location
    tags: union(tags, { 'azd-service-name': serviceName })
    identityName: identityName
    containerAppsEnvironmentName: containerAppsEnvironmentName
    containerRegistryName: containerRegistryName
    exists: true
    ingressEnabled: true
    secrets: secrets
    targetPort: 8000
    env: union(env, [
      {
        name: 'ENTRA_PROD_CLIENT_ID'
        value: entraAppClientId
      }
    ])
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
