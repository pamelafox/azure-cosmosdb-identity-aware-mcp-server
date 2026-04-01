"""
Post-provision script to add a federated identity credential (FIC) to the
production app registration, linking the managed identity created by Bicep
to the Entra app created by auth_init.py.

Both app registrations are created by auth_init.py (preprovision).
This script only adds the FIC, which requires the managed identity principal ID
output by Bicep provisioning.
"""

import asyncio
import os

from azure.identity.aio import AzureDeveloperCliCredential
from dotenv_azd import load_azd_env
from msgraph import GraphServiceClient
from msgraph.generated.applications.applications_request_builder import ApplicationsRequestBuilder
from msgraph.generated.models.federated_identity_credential import FederatedIdentityCredential


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

    await add_federated_identity_credential(graph_client)


if __name__ == "__main__":
    asyncio.run(main())
