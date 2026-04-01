"""
Pre-provision script to create the production Entra app registration.

This runs before Bicep provisioning to create the app registration and save
ENTRA_PROD_CLIENT_ID to azd env, so Bicep can pass it to the container app
on the first deploy (no circular dependency).

FIC (federated identity credential) is NOT created here because the managed
identity doesn't exist yet — that's done by auth_postprovision.py after Bicep runs.
"""

import asyncio
import os
import subprocess

from azure.identity.aio import AzureDeveloperCliCredential
from dotenv_azd import load_azd_env
from msgraph import GraphServiceClient
from msgraph.generated.applications.applications_request_builder import ApplicationsRequestBuilder
from msgraph.generated.models.api_application import ApiApplication
from msgraph.generated.models.application import Application
from msgraph.generated.models.implicit_grant_settings import ImplicitGrantSettings
from msgraph.generated.models.o_auth2_permission_grant import OAuth2PermissionGrant
from msgraph.generated.models.permission_scope import PermissionScope
from msgraph.generated.models.pre_authorized_application import PreAuthorizedApplication
from msgraph.generated.models.required_resource_access import RequiredResourceAccess
from msgraph.generated.models.resource_access import ResourceAccess
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.web_application import WebApplication
from msgraph.generated.service_principals.service_principals_request_builder import (
    ServicePrincipalsRequestBuilder,
)

PROD_APP_DISPLAY_NAME = "MCP Expense Server App"
VSCODE_CLIENT_ID = "aebc6443-996d-45c2-90f0-388ff96faa56"
USER_IMPERSONATION_SCOPE_ID = "a40e5a53-cdc1-4a42-90c0-e0f9dce78e9b"

# Redirect URIs matching the Bicep configuration
WEB_REDIRECT_URIS = [
    "https://vscode.dev/redirect",
    "http://localhost",
]


def update_azd_env(name: str, val: str) -> None:
    subprocess.run(["azd", "env", "set", name, val], check=True)


async def find_existing_app(
    graph_client: GraphServiceClient, display_name: str
) -> Application | None:
    """Find an existing app registration by display name."""
    query_params = ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
        filter=f"displayName eq '{display_name}'"
    )
    request_config = ApplicationsRequestBuilder.ApplicationsRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    apps = await graph_client.applications.get(request_configuration=request_config)
    if apps and apps.value:
        return apps.value[0]
    return None


async def create_prod_app(graph_client: GraphServiceClient) -> tuple[str, str]:
    """Create the production app registration. Returns (object_id, client_id)."""
    print("Creating production app registration...")
    new_app = Application(
        display_name=PROD_APP_DISPLAY_NAME,
        sign_in_audience="AzureADMyOrg",
        group_membership_claims="SecurityGroup",
        web=WebApplication(
            redirect_uris=WEB_REDIRECT_URIS,
            implicit_grant_settings=ImplicitGrantSettings(enable_id_token_issuance=True),
        ),
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
                    admin_consent_description="Allows access to the MCP server as the signed-in user.",
                    admin_consent_display_name="Access MCP Server",
                    id=USER_IMPERSONATION_SCOPE_ID,
                    is_enabled=True,
                    type="User",
                    user_consent_description="Allow access to the MCP server on your behalf",
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
        raise ValueError("Failed to create production app registration")
    object_id = created_app.id
    client_id = created_app.app_id
    print(f"Created production app registration: {client_id}")

    # Set the identifier URI (requires the app_id, so must be done after creation)
    await graph_client.applications.by_application_id(object_id).patch(
        Application(identifier_uris=[f"api://{client_id}"])
    )
    print(f"Set identifier URI: api://{client_id}")
    return object_id, client_id


async def ensure_service_principal_and_consent(
    graph_client: GraphServiceClient, client_id: str
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
        print(f"Service principal already exists: {sp.id}")
    else:
        sp = await graph_client.service_principals.post(
            ServicePrincipal(app_id=client_id, display_name=PROD_APP_DISPLAY_NAME)
        )
        if sp is None or sp.id is None:
            raise ValueError("Failed to create service principal")
        print(f"Created service principal: {sp.id}")

    # Check if admin consent already granted
    from msgraph.generated.oauth2_permission_grants.oauth2_permission_grants_request_builder import (
        Oauth2PermissionGrantsRequestBuilder,
    )

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
        print("Admin consent already granted.")
        return

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
    print("Admin consent granted.")


async def main():
    load_azd_env(override=True)

    # Skip if already configured
    existing_client_id = os.environ.get("ENTRA_PROD_CLIENT_ID")
    if existing_client_id:
        print(f"Production app already configured: {existing_client_id}")
        return

    auth_tenant = os.environ["AZURE_TENANT_ID"]
    credential = AzureDeveloperCliCredential(tenant_id=auth_tenant)
    graph_client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])

    # Check if app already exists (e.g. from a failed previous run)
    existing_app = await find_existing_app(graph_client, PROD_APP_DISPLAY_NAME)
    if existing_app:
        client_id = existing_app.app_id
        print(f"Found existing production app: {client_id}")
    else:
        _, client_id = await create_prod_app(graph_client)

    await ensure_service_principal_and_consent(graph_client, client_id)

    update_azd_env("ENTRA_PROD_CLIENT_ID", client_id)
    print(f"Saved ENTRA_PROD_CLIENT_ID={client_id} to azd env.")


if __name__ == "__main__":
    asyncio.run(main())
