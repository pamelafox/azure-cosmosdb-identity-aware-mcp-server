#!/bin/sh
set -e

echo "Starting uvicorn with module: main:app"
exec uvicorn main:app --host 0.0.0.0 --port 8000
