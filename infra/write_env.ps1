# Define the .env file path
$ENV_FILE_PATH = ".env"


# Returns empty string if value not found or contains ERROR
function Get-AzdValue {
    param([string]$Key)
    $value = azd env get-value $Key 2>$null
    if ($value -and $value.Contains("ERROR:")) {
        return ""
    }
    return $value
}

# Write a required env var (always written)
function Write-Env {
    param([string]$Key)
    Add-Content -Path $ENV_FILE_PATH -Value "$Key=$(Get-AzdValue $Key)"
}

# Write an optional env var (only written if value is non-empty)
function Write-EnvIfSet {
    param([string]$Key)
    $value = Get-AzdValue $Key
    if ($value -and $value -ne "") {
        Add-Content -Path $ENV_FILE_PATH -Value "$Key=$value"
    }
}

# Clear the contents of the .env file
Set-Content -Path $ENV_FILE_PATH -Value $null

Add-Content -Path $ENV_FILE_PATH -Value "AZURE_TENANT_ID=$(azd env get-value AZURE_TENANT_ID)"
Add-Content -Path $ENV_FILE_PATH -Value "AZURE_COSMOSDB_ACCOUNT=$(azd env get-value AZURE_COSMOSDB_ACCOUNT)"
Add-Content -Path $ENV_FILE_PATH -Value "AZURE_COSMOSDB_DATABASE=$(azd env get-value AZURE_COSMOSDB_DATABASE)"
Add-Content -Path $ENV_FILE_PATH -Value "AZURE_COSMOSDB_USER_CONTAINER=$(azd env get-value AZURE_COSMOSDB_USER_CONTAINER)"
Add-Content -Path $ENV_FILE_PATH -Value "APPLICATIONINSIGHTS_CONNECTION_STRING=$(azd env get-value APPLICATIONINSIGHTS_CONNECTION_STRING)"
Write-EnvIfSet LOGFIRE_TOKEN
Write-Env OPENTELEMETRY_PLATFORM

# Entra proxy env vars (only if ENTRA_PROXY_AZURE_CLIENT_ID is set)
$ENTRA_PROXY_AZURE_CLIENT_ID = Get-AzdValue ENTRA_PROXY_AZURE_CLIENT_ID
if ($ENTRA_PROXY_AZURE_CLIENT_ID -and $ENTRA_PROXY_AZURE_CLIENT_ID -ne "") {
    Add-Content -Path $ENV_FILE_PATH -Value "ENTRA_PROXY_AZURE_CLIENT_ID=$ENTRA_PROXY_AZURE_CLIENT_ID"
    Write-Env ENTRA_PROXY_AZURE_CLIENT_SECRET
    Write-Env ENTRA_PROXY_MCP_SERVER_BASE_URL
    Write-EnvIfSet ENTRA_ADMIN_GROUP_ID
}
Add-Content -Path $ENV_FILE_PATH -Value "MCP_SERVER_URL=$(azd env get-value MCP_SERVER_URL)"
