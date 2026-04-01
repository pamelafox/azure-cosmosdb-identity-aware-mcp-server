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
Write-EnvIfSet ENTRA_ADMIN_GROUP_ID

# Entra dev app env vars for local development
Write-EnvIfSet ENTRA_DEV_CLIENT_ID
Write-EnvIfSet ENTRA_DEV_CLIENT_SECRET

Add-Content -Path $ENV_FILE_PATH -Value "MCP_SERVER_URL=$(azd env get-value MCP_SERVER_URL)"
