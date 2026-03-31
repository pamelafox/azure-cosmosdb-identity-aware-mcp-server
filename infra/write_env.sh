#!/bin/bash

set -e

# Define the .env file path
ENV_FILE_PATH=".env"

# Returns empty string if value not found or contains ERROR
get_azd_value() {
  local key="$1"
  local value
  value=$(azd env get-value "$key" 2>/dev/null || echo "")
  if [[ "$value" == *ERROR:* ]]; then
    echo ""
  else
    echo "$value"
  fi
}

# Write a required env var (always written)
write_env() {
  local key="$1"
  echo "${key}=$(get_azd_value "$key")" >> "$ENV_FILE_PATH"
}

# Write an optional env var (only written if value is non-empty)
write_env_if_set() {
  local key="$1"
  local value
  value=$(get_azd_value "$key")
  if [ -n "$value" ]; then
    echo "${key}=${value}" >> "$ENV_FILE_PATH"
  fi
}

# Clear the contents of the .env file
> "$ENV_FILE_PATH"

echo "AZURE_TENANT_ID=$(azd env get-value AZURE_TENANT_ID)" >> "$ENV_FILE_PATH"
echo "AZURE_COSMOSDB_ACCOUNT=$(azd env get-value AZURE_COSMOSDB_ACCOUNT)" >> "$ENV_FILE_PATH"
echo "AZURE_COSMOSDB_DATABASE=$(azd env get-value AZURE_COSMOSDB_DATABASE)" >> "$ENV_FILE_PATH"
echo "AZURE_COSMOSDB_USER_CONTAINER=$(azd env get-value AZURE_COSMOSDB_USER_CONTAINER)" >> "$ENV_FILE_PATH"
echo "APPLICATIONINSIGHTS_CONNECTION_STRING=$(azd env get-value APPLICATIONINSIGHTS_CONNECTION_STRING)" >> "$ENV_FILE_PATH"
write_env_if_set LOGFIRE_TOKEN
write_env OPENTELEMETRY_PLATFORM
write_env_if_set ENTRA_ADMIN_GROUP_ID

# Entra proxy env vars for local development (re-use the app registration created by Bicep)
write_env_if_set ENTRA_PROXY_AZURE_CLIENT_ID
write_env_if_set ENTRA_PROXY_AZURE_CLIENT_SECRET

echo "MCP_SERVER_URL=$(azd env get-value MCP_SERVER_URL)" >> "$ENV_FILE_PATH"
