"""
Pre-provision script to create both Entra app registrations:
1. Production app — used by the deployed container app (FIC added post-provision)
2. Local dev app — used for local development with a client secret

This runs before Bicep provisioning so ENTRA_PROD_CLIENT_ID is available
to Bicep on the first deploy (no circular dependency).
"""

import asyncio
import os
import subprocess
import uuid

from azure.identity.aio import AzureDeveloperCliCredential
from dotenv_azd import load_azd_env
from msgraph import GraphServiceClient
from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
    AddPasswordPostRequestBody,
)
from msgraph.generated.models.api_application import ApiApplication
from msgraph.generated.models.application import Application
from msgraph.generated.models.o_auth2_permission_grant import OAuth2PermissionGrant
from msgraph.generated.models.password_credential import PasswordCredential
from msgraph.generated.models.permission_scope import PermissionScope
from msgraph.generated.models.pre_authorized_application import PreAuthorizedApplication
from msgraph.generated.models.required_resource_access import RequiredResourceAccess
from msgraph.generated.models.resource_access import ResourceAccess
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.web_application import WebApplication
from msgraph.generated.oauth2_permission_grants.oauth2_permission_grants_request_builder import (
    Oauth2PermissionGrantsRequestBuilder,
)
from msgraph.generated.service_principals.service_principals_request_builder import (
    ServicePrincipalsRequestBuilder,
)

VSCODE_CLIENT_ID = "aebc6443-996d-45c2-90f0-388ff96faa56"

# MS Graph API well-known IDs
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GRAPH_SCOPES = [
    ResourceAccess(id="e1fe6dd8-ba31-4d61-89e7-88639da4683d", type="Scope"),  # User.Read
    ResourceAccess(id="7427e0e9-2fba-42fe-b0c0-848c9e6a8182", type="Scope"),  # offline_access
    ResourceAccess(id="37f7f235-527c-4136-accd-4a02d197296e", type="Scope"),  # openid
    ResourceAccess(id="14dad69e-099b-42c9-810b-d002981feec1", type="Scope"),  # profile
]


def update_azd_env(name: str, val: str) -> None:
    subprocess.run(["azd", "env", "set", name, val], check=True)


async def set_identifier_uri(graph_client: GraphServiceClient, object_id: str, client_id: str) -> None:
    """Set the identifier URI to api://{client_id} (must be done after app creation)."""
    await graph_client.applications.by_application_id(object_id).patch(
        Application(identifier_uris=[f"api://{client_id}"])
    )
    print(f"  Set identifier URI: api://{client_id}")


async def ensure_service_principal_and_consent(
    graph_client: GraphServiceClient, client_id: str, display_name: str
) -> None:
    """Ensure the app has a service principal and admin consent for Graph API OBO."""
    # Check if service principal already exists
    query_params = ServicePrincipalsRequestBuilder.ServicePrincipalsRequestBuilderGetQueryParameters(
        filter=f"appId eq '{client_id}'"
    )
    request_config = ServicePrincipalsRequestBuilder.ServicePrincipalsRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    sps = await graph_client.service_principals.get(request_configuration=request_config)
    if sps and sps.value:
        sp = sps.value[0]
        print(f"  Service principal already exists: {sp.id}")
    else:
        sp = await graph_client.service_principals.post(
            ServicePrincipal(app_id=client_id, display_name=display_name)
        )
        if sp is None or sp.id is None:
            raise ValueError("Failed to create service principal")
        print(f"  Created service principal: {sp.id}")

    # Check if admin consent already granted
    grant_params = (
        Oauth2PermissionGrantsRequestBuilder.Oauth2PermissionGrantsRequestBuilderGetQueryParameters(
            filter=f"clientId eq '{sp.id}'"
        )
    )
    grant_config = (
        Oauth2PermissionGrantsRequestBuilder.Oauth2PermissionGrantsRequestBuilderGetRequestConfiguration(
            query_parameters=grant_params
        )
    )
    existing_grants = await graph_client.oauth2_permission_grants.get(request_configuration=grant_config)
    if existing_grants and existing_grants.value:
        print("  Admin consent already granted.")
        return

    # Find the Graph API service principal
    graph_sp = await graph_client.service_principals_with_app_id(GRAPH_APP_ID).get()
    if graph_sp is None or graph_sp.id is None:
        raise ValueError("Failed to find Graph API service principal")

    print("  Granting admin consent for Graph API scopes (OBO flow)...")
    await graph_client.oauth2_permission_grants.post(
        OAuth2PermissionGrant(
            client_id=sp.id,
            consent_type="AllPrincipals",
            resource_id=graph_sp.id,
            scope="User.Read email offline_access openid profile",
        )
    )
    print("  Admin consent granted.")


def make_app_body(display_name: str) -> Application:
    """Build an Application object with shared structure for both prod and local dev apps."""
    scope_id = str(uuid.uuid4())
    return Application(
        display_name=display_name,
        sign_in_audience="AzureADMyOrg",
        web=WebApplication(
            redirect_uris=["https://vscode.dev/redirect", "http://localhost"],
        ),
        # Declares Graph API permissions on the app registration--
        # Not strictly needed as admin consent is granted programmatically later
        required_resource_access=[
            RequiredResourceAccess(
                resource_app_id=GRAPH_APP_ID,
                resource_access=GRAPH_SCOPES,
            )
        ],
        api=ApiApplication(
            requested_access_token_version=2,
            oauth2_permission_scopes=[
                PermissionScope(
                    admin_consent_description="Allows access to the MCP server as the signed-in user.",
                    admin_consent_display_name="Access MCP Server",
                    id=scope_id,
                    is_enabled=True,
                    type="User",
                    user_consent_description="Allow access to the MCP server on your behalf.",
                    user_consent_display_name="Access MCP Server",
                    value="user_impersonation",
                )
            ],
            pre_authorized_applications=[
                PreAuthorizedApplication(
                    app_id=VSCODE_CLIENT_ID,
                    delegated_permission_ids=[scope_id],
                )
            ],
        ),
    )


async def create_app(
    graph_client: GraphServiceClient, app_body: Application
) -> tuple[str, str]:
    """Create an app registration. Returns (object_id, client_id)."""
    created_app = await graph_client.applications.post(app_body)
    if created_app is None:
        raise ValueError(f"Failed to create app registration: {app_body.display_name}")
    print(f"  Created app registration: {created_app.app_id}")
    return created_app.id, created_app.app_id


async def create_client_secret(graph_client: GraphServiceClient, object_id: str) -> str:
    """Create a client secret for the app registration. Returns the secret text."""
    request_password = AddPasswordPostRequestBody(
        password_credential=PasswordCredential(display_name="LocalDevSecret"),
    )
    password_credential = await graph_client.applications.by_application_id(object_id).add_password.post(
        request_password
    )
    if password_credential is None or password_credential.secret_text is None:
        raise ValueError("Failed to create client secret")
    print("  Created client secret.")
    return password_credential.secret_text


# --- Production app ---
async def create_prod_app(graph_client: GraphServiceClient) -> str:
    """Create the production app, set identifier URI, ensure SP + consent.
    Returns the client_id."""
    print("Production app:")
    PROD_APP_DISPLAY_NAME = "MCP Expense Server App (Prod)"
    app_body = make_app_body(display_name=PROD_APP_DISPLAY_NAME)
    object_id, client_id = await create_app(graph_client, app_body)
    await set_identifier_uri(graph_client, object_id, client_id)
    await ensure_service_principal_and_consent(graph_client, client_id, PROD_APP_DISPLAY_NAME)
    return client_id


# --- Local dev app ---
async def create_local_dev_app(graph_client: GraphServiceClient) -> tuple[str, str]:
    """Create the local dev app with a client secret.
    Returns (client_id, client_secret)."""
    print("Local dev app:")
    LOCAL_DEV_APP_DISPLAY_NAME = "MCP Expense Server (Local)"
    app_body = make_app_body(display_name=LOCAL_DEV_APP_DISPLAY_NAME)
    object_id, client_id = await create_app(graph_client, app_body)
    await set_identifier_uri(graph_client, object_id, client_id)
    await ensure_service_principal_and_consent(graph_client, client_id, LOCAL_DEV_APP_DISPLAY_NAME)
    secret = await create_client_secret(graph_client, object_id)
    return client_id, secret


async def main():
    load_azd_env(override=True)

    auth_tenant = os.environ["AZURE_TENANT_ID"]
    credential = AzureDeveloperCliCredential(tenant_id=auth_tenant)
    graph_client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])

    # Production app
    existing_prod = os.environ.get("ENTRA_PROD_CLIENT_ID")
    if existing_prod:
        print(f"Production app already configured: {existing_prod}")
    else:
        prod_client_id = await create_prod_app(graph_client)
        update_azd_env("ENTRA_PROD_CLIENT_ID", prod_client_id)
        print(f"Saved ENTRA_PROD_CLIENT_ID={prod_client_id}")

    # Local dev app
    if os.getenv("ENTRA_DEV_CLIENT_ID") and os.getenv("ENTRA_DEV_CLIENT_SECRET"):
        print(f"Local dev app already configured: {os.environ['ENTRA_DEV_CLIENT_ID']}")
    else:
        dev_client_id, dev_secret = await create_local_dev_app(graph_client)
        update_azd_env("ENTRA_DEV_CLIENT_ID", dev_client_id)
        update_azd_env("ENTRA_DEV_CLIENT_SECRET", dev_secret)
        print(f"Saved ENTRA_DEV_CLIENT_ID={dev_client_id}")


if __name__ == "__main__":
    asyncio.run(main())
