# Pre-provision hook to set up Azure/Entra ID app registration for FastMCP Entra OAuth Proxy

Write-Host "Setting up Entra ID app registration for FastMCP Entra OAuth Proxy..."
python ./infra/auth_init.py
