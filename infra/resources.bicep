// Everything PitchLens runs on, in one resource group (called from main.bicep).
//
//   Microsoft Foundry (Azure AI Services)  model deployments (fast, quality) + Azure AI Content Safety
//   Azure Container Apps                   engine (FastAPI, agents, MCP) and web (Next.js)
//   Azure Container Registry               images, pulled with the managed identity
//   Key Vault                              third-party secrets, referenced by the container apps
//   Log Analytics + Application Insights   logs, traces, per-stage latency
//
// One user-assigned managed identity runs both apps. It gets just the roles it needs (pull images, call
// the models and Content Safety, read Key Vault secrets). Local (key) auth on the AI account is disabled.

param environmentName string
param location string
param tags object
param principalId string
param llmProvider string
param fastModelName string
param fastModelVersion string
param qualityModelName string
param qualityModelVersion string
param modelCapacity int
@secure()
param groqApiKey string
param engineImageName string
param webImageName string

@description('Engine replicas: live match sessions are in memory, so exactly one (scale-out needs Event Hubs, T-71).')
param engineReplicas int = 1
@description('Web minimum replicas: 0 scales to zero when idle (a few seconds of cold start).')
param webMinReplicas int = 0

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var engineName = 'ca-engine-${token}'
var webName = 'ca-web-${token}'
var placeholderImage = 'mcr.microsoft.com/dotnet/samples:aspnetapp' // listens on 8080, like our images
var deployQuality = qualityModelName != fastModelName || qualityModelVersion != fastModelVersion
var fastDeploymentName = 'pitchlens-fast'
var qualityDeploymentName = deployQuality ? 'pitchlens-quality' : fastDeploymentName

// Built-in role definition IDs: public and the same in every tenant
// (https://learn.microsoft.com/azure/role-based-access-control/built-in-roles).
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87ef14b5df'
  vaultUser: '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
}

// ---------------------------------------------------------------- monitoring
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------------------------------------------------------------- identity and registry
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

// ---------------------------------------------------------------- Microsoft Foundry: models + Content Safety
resource ai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'ai-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'ai-${token}' // required for Entra ID auth
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true // managed identity / Entra ID only, no keys
  }
}

resource fastModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: ai
  name: fastDeploymentName
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: fastModelName, version: fastModelVersion }
  }
}

resource qualityModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployQuality) {
  parent: ai
  name: 'pitchlens-quality'
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: qualityModelName, version: qualityModelVersion }
  }
  dependsOn: [fastModel] // deployments on one account go one at a time
}

resource appModelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, identity.id, roles.openAiUser)
  scope: ai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}

resource appSafetyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, identity.id, roles.cognitiveServicesUser)
  scope: ai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

// The deploying user can run the engine locally against the same models and Content Safety.
resource userModelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(ai.id, principalId, roles.openAiUser)
  scope: ai
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}

resource userSafetyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(ai.id, principalId, roles.cognitiveServicesUser)
  scope: ai
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

// ---------------------------------------------------------------- Key Vault
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${token}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

var groqSecretName = 'groq-api-key'

resource groqSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(groqApiKey)) {
  parent: vault
  name: groqSecretName
  properties: { value: groqApiKey }
}

resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, roles.vaultUser)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.vaultUser)
  }
}

// ---------------------------------------------------------------- Container Apps
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

var engineFqdn = '${engineName}.${environment.properties.defaultDomain}'
var webFqdn = '${webName}.${environment.properties.defaultDomain}'
var openAiEndpoint = 'https://ai-${token}.openai.azure.com/'

var groqSecrets = empty(groqApiKey)
  ? []
  : [{ name: 'groq-api-key', keyVaultUrl: '${vault.properties.vaultUri}secrets/${groqSecretName}', identity: identity.id }]
var groqEnv = empty(groqApiKey) ? [] : [{ name: 'GROQ_API_KEY', secretRef: 'groq-api-key' }]

var engineEnv = concat(
  [
    { name: 'LLM_PROVIDER', value: llmProvider }
    { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
    { name: 'AZURE_OPENAI_FAST_DEPLOYMENT', value: fastDeploymentName }
    { name: 'AZURE_OPENAI_QUALITY_DEPLOYMENT', value: qualityDeploymentName }
    { name: 'AZURE_OPENAI_API_VERSION', value: '2024-10-21' }
    { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential -> this identity
    { name: 'CONTENT_SAFETY_ENDPOINT', value: ai.properties.endpoint }
    { name: 'CORS_ORIGINS', value: 'https://${webFqdn}' }
    { name: 'MCP_ALLOWED_HOSTS', value: engineFqdn }
    { name: 'TRUST_PROXY_HEADERS', value: '1' }
    { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
  ],
  groqEnv
)

// TCP probes work for the placeholder image too; the readiness probe needs our engine.
var tcpProbes = [
  { type: 'Startup', tcpSocket: { port: 8080 }, initialDelaySeconds: 3, periodSeconds: 5, failureThreshold: 30 }
  { type: 'Liveness', tcpSocket: { port: 8080 }, periodSeconds: 30, failureThreshold: 3 }
]
var engineProbes = empty(engineImageName)
  ? tcpProbes
  : concat(tcpProbes, [
      {
        type: 'Readiness'
        httpGet: { path: '/api/v1/health/ready', port: 8080 }
        periodSeconds: 15
        failureThreshold: 3
      }
    ])

resource engine 'Microsoft.App/containerApps@2024-03-01' = {
  name: engineName
  location: location
  tags: union(tags, { 'azd-service-name': 'engine' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8080, transport: 'auto', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: concat(
        [{ name: 'appinsights-connection-string', value: appInsights.properties.ConnectionString }],
        groqSecrets
      )
    }
    template: {
      containers: [
        {
          name: 'engine'
          image: empty(engineImageName) ? placeholderImage : engineImageName
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: engineEnv
          probes: engineProbes
        }
      ]
      scale: { minReplicas: engineReplicas, maxReplicas: engineReplicas }
    }
  }
  dependsOn: [acrPull, vaultAccess, appModelAccess, appSafetyAccess, groqSecret]
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: webName
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8080, transport: 'auto', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: empty(webImageName) ? placeholderImage : webImageName
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [{ name: 'PORT', value: '8080' }]
          probes: tcpProbes
        }
      ]
      scale: {
        minReplicas: webMinReplicas
        maxReplicas: 3
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '50' } } }]
      }
    }
  }
  dependsOn: [acrPull]
}

output registryEndpoint string = registry.properties.loginServer
output openAiEndpoint string = openAiEndpoint
output fastDeployment string = fastDeploymentName
output qualityDeployment string = qualityDeploymentName
output contentSafetyEndpoint string = ai.properties.endpoint
output apiBaseUrl string = 'https://${engineFqdn}/api/v1'
output webUrl string = 'https://${webFqdn}'
output mcpUrl string = 'https://${engineFqdn}/mcp'
