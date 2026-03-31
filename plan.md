# Presentation and code plan

Title: "Know your user: Identity-aware MCP servers with Cosmos DB"

Abstract:

Model Context Protocol (MCP) enables AI agents to securely access external data and services, but how can MCP servers store per-user data when multiple users share the same server?

In this session, we'll build a Python MCP server that authenticates users via Entra ID and stores their data in Azure Cosmos DB. I will walk through how to integrate OAuth with the FastMCP framework, use the Cosmos DB Python SDK, design partition keys for efficient per-user data isolation, and implement role-based access using Microsoft Graph group membership. I'll also share development tips when working with Cosmos DB: using the VS Code extension to explore data, chatting in GitHub Copilot with the data via the Cosmos MCP server, and empowering Copilot with agent skills to align the server code with Cosmos DB best practices.

Walk away with a production-ready pattern for building authenticated MCP servers that keep each user's data private and secure.

Presentation outline:

* Start with a demo of the final MCP server in action, showing how it securely stores and retrieves per-user data from Cosmos DB. Demo in VS Code, already logged in.
* Slides: What is MCP?
* Slides: MCP server architecture overview (for this demo)
* Slides: MCP authentication options overview
* Code walkthrough: Setting up FastMCP server with OAuth authentication
* Demo: Go through full auth flow in VS Code (reset the OAuth credential providers in VS Code first)
* Slides: Integrating Cosmos DB with MCP server
* Slides: Designing partition keys for per-user data isolation
* Code walkthrough: Using Cosmos DB Python SDK to store per-user data
* Demo: Show Cosmos DB data in VS Code extension, query per-user data
* Demo: Chat with GitHub Copilot in VS Code, using the Cosmos MCP server to access data
* Slides: Implementing role-based access with Microsoft Graph group membership
* Code walkthrough: Implementing role-based access with Microsoft Graph group membership
* Demo: Use a tool that requires elevated permissions, show how access is granted/denied based on group membership
* Slides: Tips for developing with Cosmos DB and MCP servers
* Demo: Use GitHub Copilot agent skills to analyze Cosmos DB data and optimize queries
* Conclusion and next steps for building your own identity-aware MCP servers with Cosmos DB

## MCP auth approach with Entra ID

The MCP spec (2025-11-25) defines OAuth 2.1 as the auth mechanism. MCP clients can identify themselves via:

* **Dynamic Client Registration (DCR)**: Client registers on-the-fly with the server's `/register` endpoint
* **Client ID Metadata Documents (CIMD)**: Client sends a URL as its `client_id`; server fetches metadata from that URL (IETF draft `draft-parecki-oauth-client-id-metadata-document`)

VS Code uses CIMD — it sends `client_id=https://vscode.dev/oauth/client-metadata.json`.
Entra ID only accepts GUID client IDs, not URLs. This creates a fundamental tension.

### Option 1: Full OAuth Proxy with DCR (FastMCP `AzureProvider`)

FastMCP's `AzureProvider` acts as a full OAuth proxy — it hosts `/authorize`, `/token`, `/register`, `/.well-known/oauth-authorization-server`, etc. The MCP client thinks the MCP server IS the auth server. Behind the scenes, FastMCP translates to Entra.

* **DCR flow**: Client calls `/register` → FastMCP creates a `ProxyDCRClient` (stored in a key-value store like CosmosDB) → client uses the returned `client_id` for subsequent OAuth calls
* **CIMD flow**: Client sends URL as `client_id` → FastMCP fetches the CIMD document → creates a synthetic `ProxyDCRClient` from it (no `/register` call needed)

✅ Pros: Works with ANY MCP client (DCR or CIMD). Server is self-contained.
❌ Cons: Entra team dislikes DCR because arbitrary clients can register. Requires a client secret for the Entra app registration (for the proxy to exchange codes with Entra). Server hosts many OAuth endpoints.

### Option 2: OAuth Proxy with CIMD only (FastMCP `AzureProvider` + `enable_cimd=True`)

Same as Option 1 but we only enable CIMD, not DCR. FastMCP still proxies the OAuth flow, but only accepts URL-based client IDs (like VS Code's). Unknown clients can't dynamically register.

* Uses `require_authorization_consent="external"` so FastMCP skips its own consent page and redirects straight to Entra's login/consent
* Still needs a client secret for the proxy to exchange codes with Entra
* FastMCP hosts `/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/auth/callback`
* No `/register` endpoint exposed

✅ Pros: More secure than full DCR (only known CIMD clients accepted). Works with VS Code.
❌ Cons: Still requires a client secret. Server still proxies all OAuth endpoints. Only works with CIMD-capable clients.

### Option 3: `RemoteAuthProvider` with `AzureJWTVerifier` (direct Entra auth)

FastMCP's `RemoteAuthProvider` serves only `/.well-known/oauth-protected-resource` (PRM endpoint per RFC 9728), pointing the client directly to Entra's authorization server. The MCP client talks to Entra directly — no proxy.

```python
verifier = AzureJWTVerifier(
    client_id=os.environ["ENTRA_CLIENT_ID"],
    tenant_id=os.environ["AZURE_TENANT_ID"],
    required_scopes=["user_impersonation"],
)
auth = RemoteAuthProvider(
    token_verifier=verifier,
    authorization_servers=[f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}/v2.0"],
    base_url="http://localhost:8000",
)
```

PRM response looks like:

```json
{
  "resource": "http://localhost:8000/mcp",
  "authorization_servers": ["https://login.microsoftonline.com/{tenant}/v2.0"],
  "scopes_supported": ["api://{client-id}/user_impersonation"],
  "bearer_methods_supported": ["header"]
}
```

✅ Pros: No proxy, no client secret needed on the server. Server just validates JWTs. Cleanest architecture.
❌ Cons: VS Code sends `client_id=https://vscode.dev/oauth/client-metadata.json` to Entra's `/authorize`, and Entra rejects URL-based client IDs. **Unless** combined with Option 4 (pre-registration).

### Option 4: Pre-register VS Code's GUID in Entra + `RemoteAuthProvider`

Register VS Code's known Entra app GUID (`aebc6443-996d-45c2-90f0-388ff96faa56`) as a `preAuthorizedApplication` on the server's app registration. This allows VS Code to call the server on behalf of the signed-in user.

**Initial assumption was wrong**: We assumed VS Code would always send the CIMD URL to Entra. In reality, when the PRM's `authorization_servers` points directly to Entra, VS Code recognizes this and uses its own first-party Entra GUID (`aebc6443-...`) via the macOS/Windows authentication broker (Company Portal / WAM). It does NOT send the CIMD URL to Entra.

**Requirements for this to work**:

1. App registration must have `identifierUris: ['api://{appId}']` (establishes the `api://` namespace)
2. App registration must expose a `user_impersonation` OAuth2 permission scope
3. VS Code's GUID must be in `preAuthorizedApplications` with the `user_impersonation` scope ID
4. App manifest must have `requestedAccessTokenVersion: 2` (FastMCP's `AzureJWTVerifier` only supports v2.0 tokens)
5. Must use explicit tenant ID in `authorization_servers` (not `/common` — see [VS Code issue #283453](https://github.com/microsoft/vscode/issues/283453))

**App registration Bicep** (key parts):

```bicep
resource appRegistration 'Microsoft.Graph/applications@v1.0' = {
  displayName: 'MCP Expense Server'
  uniqueName: 'mcp-expense-server-${resourceToken}'
  identifierUris: ['api://${appRegistration.appId}']
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: [{
      id: scopeId
      value: 'user_impersonation'
      type: 'User'
      adminConsentDisplayName: 'Access MCP server'
      adminConsentDescription: 'Allow the app to access the MCP server on your behalf'
    }]
    preAuthorizedApplications: [{
      appId: 'aebc6443-996d-45c2-90f0-388ff96faa56'  // VS Code
      delegatedPermissionIds: [scopeId]
    }]
  }
}
```

**Gotchas we hit**:

* Without `identifierUris`, `preAuthorizedApplications` silently fails to appear in the portal
* Without `requestedAccessTokenVersion: 2`, Entra issues v1.0 tokens which `AzureJWTVerifier` rejects
* The Bicep Microsoft Graph extension may not update `preAuthorizedApplications` on existing app registrations — may need to delete and recreate
* On macOS, VS Code uses the Company Portal broker — initial attempt failed with `platform_broker_error` because pre-authorization wasn't applied yet

✅ Pros: No proxy, no client secret on server. Server just validates JWTs. Works with VS Code (tested!). Cleanest option.
❌ Cons: Requires pre-registering each MCP client's GUID (only works with known clients). Local dev still needs a client secret for OBO/Graph calls (separate app registration).

### Verified: Option 3 + 4 combined works locally

Tested successfully on 2026-03-31. Logs show VS Code authenticating directly with Entra (no proxy endpoints):

```text
INFO:     127.0.0.1:54421 - "POST /mcp HTTP/1.1" 401 Unauthorized
INFO:     Auth error returned: invalid_token (status=401)
INFO:     127.0.0.1:54422 - "GET /.well-known/oauth-protected-resource/mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:54421 - "POST /mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:54421 - "GET /mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:54422 - "POST /mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:54426 - "POST /mcp HTTP/1.1" 202 Accepted
INFO:     ExpensesMCP: Adding expense: $20.0 for Avocado toast on 2026-03-31
```

Key differences from proxy logs: No `/authorize`, `/token`, `/consent`, or `/auth/callback` on the server. VS Code went directly to Entra, got a token, and sent it to `/mcp`.

### Option 5: Azure App Service / Container Apps / Functions Built-in Auth (Easy Auth)

The hosting platform handles OAuth at the infrastructure level. Configure Easy Auth with:

* `WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES` to serve the PRM endpoint
* Pre-authorize VS Code's client ID (`aebc6443-996d-45c2-90f0-388ff96faa56`) on the app registration
* Easy Auth handles the CIMD-to-Entra-GUID translation at the platform level
* No client secret needed — uses Federated Identity Credential (FIC) with managed identity
* Server receives authenticated requests with `X-MS-CLIENT-PRINCIPAL` / `X-MS-CLIENT-PRINCIPAL-ID` headers

Docs: <https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp-server-vscode>

✅ Pros: Auth at infrastructure layer (defense in depth). Keyless (FIC, no secrets). Platform handles CIMD translation. Works with App Service, Container Apps, Functions.
❌ Cons: Can't test locally (no Easy Auth locally). Only works with pre-authorized clients.

### Our approach: Option 3+4 (local and production)

**Primary approach**: `RemoteAuthProvider` + `AzureJWTVerifier` + pre-registered VS Code GUID. Works both locally and in production — same code path everywhere.

* **Server code**: Uses `RemoteAuthProvider` to serve PRM, `AzureJWTVerifier` to validate Entra v2.0 JWTs. No proxy, no Easy Auth headers, no environment detection needed for auth.
* **Entra app registration**: Exposes `user_impersonation` scope, pre-authorizes VS Code's GUID, sets `requestedAccessTokenVersion: 2`.
* **Local dev app**: Separate app registration with client secret (for OBO/Graph API calls). Created by `infra/auth_postprovision.py`.
* **Production app**: Created by Bicep (`infra/appregistration.bicep`). Uses FIC with managed identity for Cosmos DB access (but no client secret needed for MCP auth — server just validates JWTs).

**Fallback option kept in Bicep**: Easy Auth (Option 5) configuration remains in the Bicep templates as an alternative. Could be useful if we need defense-in-depth or want to support non-pre-authorized clients.

**Security**: Two separate app registrations — a leaked local dev secret can't impersonate the production app.

### CIMD logs

```text
INFO:     127.0.0.1:51085 - "GET / HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:51085 - "GET /favicon.ico HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:51190 - "POST /mcp HTTP/1.1" 401 Unauthorized
[03/30/26 21:38:19] INFO     Auth error returned: invalid_token (status=401)   middleware.py:92
INFO:     127.0.0.1:51191 - "GET /.well-known/oauth-protected-resource/mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:51190 - "GET /.well-known/oauth-authorization-server HTTP/1.1" 200 OK
[03/30/26 21:38:29] INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json
                             (client_name=Visual Studio Code)
INFO:     127.0.0.1:51193 - "GET /authorize?client_id=...&response_type=code&..." 302 Found
                    INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json
                             (client_name=Visual Studio Code)
INFO:     127.0.0.1:51193 - "GET /consent?txn_id=...&prompt=select_account HTTP/1.1" 200 OK
INFO:     127.0.0.1:51195 - "POST /consent?txn_id=...&prompt=select_account HTTP/1.1" 302 Found
INFO:     127.0.0.1:51195 - "GET /auth/callback?code=abc HTTP/1.1" 302 Found
[03/30/26 21:39:04] INFO     CIMD document fetched and validated:                   cimd.py:402
[03/30/26 21:39:05] INFO     CIMD document fetched and validated:                   cimd.py:402
INFO:     127.0.0.1:51342 - "POST /token HTTP/1.1" 200 OK
INFO:     127.0.0.1:51342 - "POST /mcp HTTP/1.1" 200 OK
```

#### CIMD logs - with external consent

```text
INFO:     127.0.0.1:52070 - "POST /mcp HTTP/1.1" 401 Unauthorized
[03/30/26 21:44:30] INFO     Auth error returned: invalid_token (status=401)             middleware.py:92
INFO:     127.0.0.1:52071 - "GET /.well-known/oauth-protected-resource/mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:52070 - "GET /.well-known/oauth-authorization-server HTTP/1.1" 200 OK
[03/30/26 21:44:40] INFO     CIMD document fetched and validated:                             cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json
                             (client_name=Visual Studio Code)
INFO:     127.0.0.1:52102 - "GET /authorize?client_id=...&response_type=code&..." 302 Found
INFO:     127.0.0.1:52104 - "GET /auth/callback?code=abc HTTP/1.1" 302 Found
[03/30/26 21:44:51] INFO     CIMD document fetched and validated:                             cimd.py:402
                    INFO     CIMD document fetched and validated:                             cimd.py:402
INFO:     127.0.0.1:52145 - "POST /token HTTP/1.1" 200 OK
INFO:     127.0.0.1:52145 - "POST /mcp HTTP/1.1" 200 OK
```

## Role-based access approach

We will use Microsoft Graph API to check the user's group membership and determine their role. This is a common approach for implementing role-based access control in applications that integrate with Entra ID. We will need to register an app in Entra ID and grant it permissions to read group membership. Then, in our MCP server code, we can call the Graph API to check if the user is a member of a specific group and grant or deny access accordingly.

FastMCP has some built-in auth decorators support now that we may want to use for this.

## Related resources

I presented on related topics for this series:
<https://aka.ms/pythonmcp/resources>

The slides may be useful.

## Open questions

1) Should we do something fun with MCP apps, now that they're supported by FastMCP 3?
