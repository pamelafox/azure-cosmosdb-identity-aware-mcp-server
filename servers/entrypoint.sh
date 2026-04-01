#!/bin/sh
set -e

: "${MCP_ENTRY:=deployed_mcp}"
APP_MODULE="${MCP_ENTRY}:app"

echo "Starting uvicorn with module: ${APP_MODULE}"
exec uvicorn "${APP_MODULE}" --host 0.0.0.0 --port 8000
