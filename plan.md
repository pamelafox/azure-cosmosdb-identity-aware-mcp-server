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

## MCP auth approach

Options:

1) Use FastMCP built-in OAuth Proxy support and full DCR. Entra team does not approve of this, due to security risks. However, it does mean that the servers will work with arbitrary MCP clients.
2) Use FastMCP support for pre-registered client IDs with Entra app registration. This will work for VS Code, where we are demoing, which has a client id of aebc6443-996d-45c2-90f0-388ff96faa5.
3) Use Azure Container Apps built-in middleware for Entra MCP Auth with pre-registered client IDs. This also works for Azure Functions and Azure Container Apps. However, that means we can't test the server locally. This has the advantage that we can use keyless auth with Entra, which is more secure and easier to manage.

Ideally, we would use both #2 and #3, so that we get local development with option #2, and production security with option #3. However, for the sake of simplicity in the demo, we will go with option #2 for now, and leave it as an exercise for the reader to implement option #3 in production. We should link to the appropriate documentation for those options.


### CIMD logs

INFO:     127.0.0.1:51085 - "GET / HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:51085 - "GET /favicon.ico HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:51190 - "POST /mcp HTTP/1.1" 401 Unauthorized
[03/30/26 21:38:19] INFO     Auth error returned: invalid_token (status=401)   middleware.py:92
INFO:     127.0.0.1:51191 - "GET /.well-known/oauth-protected-resource/mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:51190 - "GET /.well-known/oauth-authorization-server HTTP/1.1" 200 OK
[03/30/26 21:38:29] INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                     
                             (client_name=Visual Studio Code)                                  
INFO:     127.0.0.1:51193 - "GET /authorize?client_id=https%3A%2F%2Fvscode.dev%2Foauth%2Fclient-metadata.json&response_type=code&code_challenge=UsVLZlrqfuY8OdmCllgF0TvtnpEZqHKlbA7E1UEN6Ac&code_challenge_method=S256&scope=mcp-access&resource=http%3A%2F%2Flocalhost%3A8000%2Fmcp&redirect_uri=http%3A%2F%2F127.0.0.1%3A33418%2F&state=gXVXo0%2FiMD8WMolDHKxSTg%3D%3D HTTP/1.1" 302 Found
                    INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                     
                             (client_name=Visual Studio Code)                                  
INFO:     127.0.0.1:51193 - "GET /consent?txn_id=wBEzD21iXpLeIwHVdZ9pWsp024BAsHEPn0NdbJH-MVo&prompt=select_account HTTP/1.1" 200 OK
INFO:     127.0.0.1:51195 - "POST /consent?txn_id=wBEzD21iXpLeIwHVdZ9pWsp024BAsHEPn0NdbJH-MVo&prompt=select_account HTTP/1.1" 302 Found
INFO:     127.0.0.1:51195 - "GET /auth/callback?code=abc HTTP/1.1" 302 Found
[03/30/26 21:39:04] INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                     
                             (client_name=Visual Studio Code)                                  
[03/30/26 21:39:05] INFO     CIMD document fetched and validated:                   cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                     
                             (client_name=Visual Studio Code)  
INFO:     127.0.0.1:51342 - "POST /token HTTP/1.1" 200 OK
INFO:     127.0.0.1:51342 - "POST /mcp HTTP/1.1" 200 OK

#### CIMD logs - with external consent

INFO:     127.0.0.1:52070 - "POST /mcp HTTP/1.1" 401 Unauthorized
[03/30/26 21:44:30] INFO     Auth error returned: invalid_token (status=401)             middleware.py:92
INFO:     127.0.0.1:52071 - "GET /.well-known/oauth-protected-resource/mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:52070 - "GET /.well-known/oauth-authorization-server HTTP/1.1" 200 OK
[03/30/26 21:44:40] INFO     CIMD document fetched and validated:                             cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                               
                             (client_name=Visual Studio Code)                                            
INFO:     127.0.0.1:52102 - "GET /authorize?client_id=https%3A%2F%2Fvscode.dev%2Foauth%2Fclient-metadata.json&response_type=code&code_challenge=Xh0nZTsLOnFzxb8CnvfsT2FyXAcIH5QlWRkBYJUHPPI&code_challenge_method=S256&scope=mcp-access&resource=http%3A%2F%2Flocalhost%3A8000%2Fmcp&redirect_uri=http%3A%2F%2F127.0.0.1%3A33418%2F&state=aA9mJ2U2e5hCpv8vUJXw2A%3D%3D HTTP/1.1" 302 Found
INFO:     127.0.0.1:52104 - "GET /auth/callback?code=abc HTTP/1.1" 302 Found
[03/30/26 21:44:51] INFO     CIMD document fetched and validated:                             cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                               
                             (client_name=Visual Studio Code)                                            
                    INFO     CIMD document fetched and validated:                             cimd.py:402
                             https://vscode.dev/oauth/client-metadata.json                               
                             (client_name=Visual Studio Code)                                            
INFO:     127.0.0.1:52145 - "POST /token HTTP/1.1" 200 OK
INFO:     127.0.0.1:52145 - "POST /mcp HTTP/1.1" 200 OK

## Role-based access approach

We will use Microsoft Graph API to check the user's group membership and determine their role. This is a common approach for implementing role-based access control in applications that integrate with Entra ID. We will need to register an app in Entra ID and grant it permissions to read group membership. Then, in our MCP server code, we can call the Graph API to check if the user is a member of a specific group and grant or deny access accordingly.

FastMCP has some built-in auth decorators support now that we may want to use for this.

## Related resources

I presented on related topics for this series:
https://aka.ms/pythonmcp/resources

The slides may be useful.

## Open questions

1) Should we do something fun with MCP apps, now that they're supported by FastMCP 3?