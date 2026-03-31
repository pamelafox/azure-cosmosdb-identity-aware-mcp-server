#!/bin/bash
# Post-provision hook to update Azure app registration redirect URIs with deployed server URL

echo "Updating FastMCP auth redirect URIs with deployed server URL..."
uv run python ./infra/auth_update.py
