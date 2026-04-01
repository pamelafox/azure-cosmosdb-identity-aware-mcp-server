"""
Post-provision script to:
1. Add a federated identity credential (FIC) to the production app registration,
   linking the managed identity created by Bicep to the Entra app created by auth_init.py.
2. Create a separate Entra app registration for local development with a client secret.
"""

import asyncio
import os
import subprocess

from azure.identity.aio import AzureDeveloperCliCredential
from dotenv_azd import load_azd_env
from msgraph import GraphServiceClient
from msgraph.generated.applications.applications_request_builder import ApplicationsRequestBuilder
from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
    AddPasswordPostRequestBody,
)
from msgraph.generated.models.api_application import ApiApplication
from msgraph.generated.models.application import Application
from msgraph.generated.models.federated_identity_credential import FederatedIdentityCredential
from msgraph.generated.models.o_auth2_permission_grant import OAuth2PermissionGrant
from msgraph.generated.models.password_credential import PasswordCredential
from msgraph.generated.models.permission_scope import PermissionScope
from msgraph.generated.models.pre_authorized_application import PreAuthorizedApplication
from msgraph.generated.models.required_resource_access import RequiredResourceAccess
from msgraph.generated.models.resource_access import ResourceAccess
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.spa_application import SpaApplication
from msgraph.generated.models.web_application import WebApplication

LOCAL_DEV_APP_DISPLAY_NAME = "MCP Expense Server (Local Dev)"
LOCAL_REDIRECT_URIS = [
    "http://localhost:8000/auth/callback",
]
VSCODE_CLIENT_ID = "aebc6443-996d-45c2-90f0-388ff96faa56"
USER_IMPERSONATION_SCOPE_ID = "c8b667c4-ea67-4634-8138-826283a5c855"


def update_azd_env(name: str, val: str) -> None:
    subprocess.run(["azd", "env", "set", name, val], check=True)


async def create_app_registration(graph_client: GraphServiceClient) -> tuple[str, str]:
    """Create a new Entra app registration for local development. Returns (object_id, client_id)."""
    print("Creating local dev app registration...")
    new_app = Application(
        display_name=LOCAL_DEV_APP_DISPLAY_NAME,
        sign_in_audience="AzureADMyOrg",
        web=WebApplication(redirect_uris=LOCAL_REDIRECT_URIS),
        spa=SpaApplication(redirect_uris=[]),
        required_resource_access=[
            RequiredResourceAccess(
                resource_app_id="00000003-0000-0000-c000-000000000000",  # MS Graph
                resource_access=[
                    ResourceAccess(id="e1fe6dd8-ba31-4d61-89e7-88639da4683d", type="Scope"),  # User.Read
                    ResourceAccess(id="7427e0e9-2fba-42fe-b0c0-848c9e6a8182", type="Scope"),  # offline_access
                    ResourceAccess(id="37f7f235-527c-4136-accd-4a02d197296e", type="Scope"),  # openid
                    ResourceAccess(id="14dad69e-099b-42c9-810b-d002981feec1", type="Scope"),  # profile
                ],
            )
        ],
        api=ApiApplication(
            requested_access_token_version=2,
            oauth2_permission_scopes=[
                PermissionScope(
                    admin_consent_description="Allow the application to access the MCP server on behalf of the signed-in user.",
                    admin_consent_display_name="Access MCP Server",
                    id=USER_IMPERSONATION_SCOPE_ID,
                    is_enabled=True,
                    type="User",
                    user_consent_description="Allow the application to access the MCP server on your behalf.",
                    user_consent_display_name="Access MCP Server",
                    value="user_impersonation",
                )
            ],
            pre_authorized_applications=[
                PreAuthorizedApplication(
                    app_id=VSCODE_CLIENT_ID,
                    delegated_permission_ids=[USER_IMPERSONATION_SCOPE_ID],
                )
            ],
        ),
    )
    created_app = await graph_client.applications.post(new_app)
    if created_app is None:
        raise ValueError("Failed to create local dev app registration")
    object_id = created_app.id
    client_id = created_app.app_id
    print(f"Created local dev app registration: {client_id}")

    # Set the identifier URI (requires the app_id, so must be done after creation)
    await graph_client.applications.by_application_id(object_id).patch(
        Application(identifier_uris=[f"api://{client_id}"])
    )
    print(f"Set identifier URI: api://{client_id}")
    return object_id, client_id


async def create_service_principal_and_grant_consent(
    graph_client: GraphServiceClient, client_id: str
) -> None:
    """Create a service principal and grant admin consent for Graph API OBO flow."""
    print("Creating service principal for local dev app...")
    sp = await graph_client.service_principals.post(
        ServicePrincipal(app_id=client_id, display_name=LOCAL_DEV_APP_DISPLAY_NAME)
    )
    if sp is None or sp.id is None:
        raise ValueError("Failed to create service principal")
    print(f"Created service principal: {sp.id}")

    # Find the Graph API service principal
    graph_app_id = "00000003-0000-0000-c000-000000000000"
    graph_sp = await graph_client.service_principals_with_app_id(graph_app_id).get()
    if graph_sp is None or graph_sp.id is None:
        raise ValueError("Failed to find Graph API service principal")

    print("Granting admin consent for Graph API scopes (OBO flow)...")
    await graph_client.oauth2_permission_grants.post(
        OAuth2PermissionGrant(
            client_id=sp.id,
            consent_type="AllPrincipals",
            resource_id=graph_sp.id,
            scope="User.Read email offline_access openid profile",
        )
    )
    print("Admin consent granted for Graph API scopes.")


async def create_client_secret(graph_client: GraphServiceClient, object_id: str) -> str:
    """Create a client secret for the app registration. Returns the secret text."""
    print("Creating client secret for local dev app...")
    request_password = AddPasswordPostRequestBody(
        password_credential=PasswordCredential(display_name="LocalDevSecret"),
    )
    password_credential = await graph_client.applications.by_application_id(object_id).add_password.post(
        request_password
    )
    if password_credential is None or password_credential.secret_text is None:
        raise ValueError("Failed to create client secret")
    return password_credential.secret_text


async def add_federated_identity_credential(graph_client: GraphServiceClient) -> None:
    """Add a FIC to the production app, linking the managed identity to the Entra app.

    This allows the container app's managed identity to act as the Entra app
    without needing a client secret (MI-as-FIC pattern).
    """
    prod_client_id = os.environ.get("ENTRA_PROD_CLIENT_ID")
    mi_principal_id = os.environ.get("SERVICE_SERVER_IDENTITY_PRINCIPAL_ID")
    tenant_id = os.environ["AZURE_TENANT_ID"]

    if not prod_client_id:
        print("No ENTRA_PROD_CLIENT_ID set, skipping FIC creation.")
        return
    if not mi_principal_id:
        print("No SERVICE_SERVER_IDENTITY_PRINCIPAL_ID set, skipping FIC creation.")
        return

    # Find the production app by its client ID
    query_params = ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
        filter=f"appId eq '{prod_client_id}'"
    )
    request_config = ApplicationsRequestBuilder.ApplicationsRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    apps = await graph_client.applications.get(request_configuration=request_config)
    if not apps or not apps.value:
        print(f"Production app not found for client ID: {prod_client_id}")
        return

    prod_app = apps.value[0]
    object_id = prod_app.id

    # Check if FIC already exists
    existing_fics = await graph_client.applications.by_application_id(
        object_id
    ).federated_identity_credentials.get()
    if existing_fics and existing_fics.value:
        for fic in existing_fics.value:
            if fic.subject == mi_principal_id:
                print(f"FIC already exists for managed identity: {mi_principal_id}")
                return

    # Create FIC
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    fic = FederatedIdentityCredential(
        name="miAsFic",
        issuer=issuer,
        subject=mi_principal_id,
        audiences=["api://AzureADTokenExchange"],
    )
    await graph_client.applications.by_application_id(
        object_id
    ).federated_identity_credentials.post(fic)
    print(f"Created FIC: issuer={issuer}, subject={mi_principal_id}")


async def main():
    load_azd_env(override=True)
    auth_tenant = os.environ["AZURE_TENANT_ID"]

    credential = AzureDeveloperCliCredential(tenant_id=auth_tenant)
    graph_client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])

    # Add FIC to production app (links managed identity to Entra app)
    await add_federated_identity_credential(graph_client)

    # Skip local dev app creation if already configured
    if os.getenv("ENTRA_DEV_CLIENT_ID") and os.getenv("ENTRA_DEV_CLIENT_SECRET"):
        print(f"Local dev app already configured: {os.environ['ENTRA_DEV_CLIENT_ID']}")
        return

    object_id, client_id = await create_app_registration(graph_client)
    update_azd_env("ENTRA_DEV_CLIENT_ID", client_id)

    await create_service_principal_and_grant_consent(graph_client, client_id)

    secret = await create_client_secret(graph_client, object_id)
    update_azd_env("ENTRA_DEV_CLIENT_SECRET", secret)

    print("Local dev app configured and secret saved to azd environment.")
    print("Run 'bash infra/write_env.sh' to update your .env file.")


if __name__ == "__main__":
    asyncio.run(main())
