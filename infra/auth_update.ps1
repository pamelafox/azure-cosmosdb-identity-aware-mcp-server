# Post-provision hook to update Azure app registration redirect URIs with deployed server URL

Write-Host "Updating FastMCP auth redirect URIs with deployed server URL..."
python ./infra/auth_update.py
