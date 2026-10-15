// PitchLens on Azure (plan §15, T-70). Deployed with the Azure Developer CLI: `azd up`.
// Creates a resource group and everything PitchLens runs on; see docs/deploy.md.
targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name; used to name resources.')
param environmentName string

@minLength(1)
@description('Region for all resources. Pick one where your subscription has Azure OpenAI quota.')
param location string

@description('Object id of the deploying user (azd sets it), granted model and safety access for local runs.')
param principalId string = ''

@description('Model provider for the engine: azure_openai (Microsoft Foundry deployments), groq, or mock.')
@allowed(['azure_openai', 'groq', 'mock'])
param llmProvider string = 'azure_openai'

@description('Fast-tier model (live narration, agents) and its version.')
param fastModelName string = 'gpt-4.1-mini'
param fastModelVersion string = '2025-04-14'

@description('Quality-tier model (recaps) and its version. Defaults to the fast model.')
param qualityModelName string = 'gpt-4.1-mini'
param qualityModelVersion string = '2025-04-14'

@description('Tokens-per-minute capacity, in thousands, for each model deployment.')
param modelCapacity int = 50

@secure()
@description('Optional Groq API key (only for llmProvider=groq); stored in Key Vault.')
param groqApiKey string = ''

@description('Images set by azd after the first deploy (empty on the very first provision).')
param engineImageName string = ''
param webImageName string = ''
param edgeImageName string = ''

var tags = { 'azd-env-name': environmentName, app: 'pitchlens' }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'pitchlens-resources'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    principalId: principalId
    llmProvider: llmProvider
    fastModelName: fastModelName
    fastModelVersion: fastModelVersion
    qualityModelName: qualityModelName
    qualityModelVersion: qualityModelVersion
    modelCapacity: modelCapacity
    groqApiKey: groqApiKey
    engineImageName: engineImageName
    webImageName: webImageName
    edgeImageName: edgeImageName
  }
}

// azd reads these outputs into its environment (and the web build reads API_BASE_URL and STREAM_BASE_URL).
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryEndpoint
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AZURE_OPENAI_FAST_DEPLOYMENT string = resources.outputs.fastDeployment
output AZURE_OPENAI_QUALITY_DEPLOYMENT string = resources.outputs.qualityDeployment
output CONTENT_SAFETY_ENDPOINT string = resources.outputs.contentSafetyEndpoint
output API_BASE_URL string = resources.outputs.apiBaseUrl
output STREAM_BASE_URL string = resources.outputs.streamBaseUrl
output WEB_URL string = resources.outputs.webUrl
output MCP_URL string = resources.outputs.mcpUrl
