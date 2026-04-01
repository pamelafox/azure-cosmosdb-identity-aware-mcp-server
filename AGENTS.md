# Instructions for coding agents

## Adding a new azd environment variable

An azd environment variable is stored by the azd CLI for each environment. It is passed to the "azd up" command and can configure both provisioning options and application settings.

When adding new azd environment variables, update these files:

1. **infra/main.parameters.json**: Add the new parameter mapping from azd env variable to Bicep parameter
   - Use format `${ENV_VAR_NAME}` for required values
   - Use format `${ENV_VAR_NAME=default}` for optional values with defaults
   - Example: `"mcpAuthProvider": { "value": "${MCP_AUTH_PROVIDER=none}" }`

2. **infra/main.bicep**: Add the Bicep parameter declaration at the top with `@description`
   - Use `@secure()` decorator for sensitive values like passwords/secrets
   - Example: `@description('Flag to enable feature X') param useFeatureX bool = false`

3. **infra/server.bicep** (or other module): If the variable needs to be passed to a container app:
   - Add a parameter to receive the value from main.bicep
   - Add the environment variable to the appropriate `env` array (e.g., `baseEnv`, or a conditional array)
   - For secrets, add to a secrets array and reference via `secretRef`

4. **infra/main.bicep**: Pass the parameter value to the module
   - Example: `featureXEnabled: useFeatureX ? someValue : ''`

5. **infra/write_env.sh** and **infra/write_env.ps1**: If the variable should be written to `.env` for local development:
   - Add a line to echo/write the value from `azd env get-value`
   - For conditional values, wrap in an if block to only write when populated

6. **infra/main.bicep outputs**: If the value needs to be stored back in azd env after provisioning:
   - Add an output (note: `@secure()` parameters cannot be outputs)

## Updating Python dependencies

When updating or adding Python dependencies:

1. Edit `pyproject.toml` with the new or updated version constraints.
2. Run `uv lock` to re-resolve dependencies (use `uv lock -P <package>` to upgrade only a specific package).
3. Run `uv sync` to install the updated lockfile into the virtual environment.

## Slide design

When editing presentation slides:

1. Section and slide headlines must fit on a single line; do not allow headline wrapping onto two lines.
2. If a headline wraps, shorten the text or move qualifiers into the supporting body copy instead of shrinking the design to force it in.
3. In any diagram with an arrow and a badge or label placed on that arrow, keep visible line on both sides of the badge so the start and end of the arrow are clearly readable.
4. Use real product logos or service icons when they are available; prefer recognizable brand visuals over generic placeholders.
5. In comparison or architecture diagrams, align equivalent labels, titles, and cards to shared baselines when possible so the layout reads cleanly.
6. Size boxes and cards for their content; maintain enough internal padding that logos, labels, and supporting text never overlap or spill outside the shape.
