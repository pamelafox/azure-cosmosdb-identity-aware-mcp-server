"""
Expense tracking MCP server with Entra authentication and Cosmos DB storage.

Uses RemoteAuthProvider with AzureJWTVerifier for direct Entra authentication.
VS Code authenticates directly with Entra (pre-registered as authorized client),
and the server validates the token using Entra's public signing keys.

Run locally with: cd servers && uv run uvicorn auth_entra_mcp:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import uuid
import warnings
from datetime import date
from enum import Enum
from typing import Annotated

import httpx
import logfire
from azure.core.settings import settings
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from fastmcp import Context, FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.azure import AzureJWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from msal import ConfidentialClientApplication, TokenCache
from opentelemetry.instrumentation.starlette import StarletteInstrumentor
from rich.console import Console
from rich.logging import RichHandler
from starlette.responses import JSONResponse

from opentelemetry_middleware import OpenTelemetryMiddleware

RUNNING_IN_PRODUCTION = os.getenv("RUNNING_IN_PRODUCTION", "false").lower() == "true"

if not RUNNING_IN_PRODUCTION:
    load_dotenv(override=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(name)s: %(message)s",
    handlers=[
        RichHandler(
            console=Console(stderr=True),
            show_path=False,
            show_level=False,
            rich_tracebacks=True,
        )
    ],
)
# Suppress OTEL 1.39 deprecation warnings and noisy logs
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r".*Deprecated since version 1\.39\.0.*")
logging.getLogger("azure.monitor.opentelemetry.exporter._performance_counters._manager").setLevel(logging.ERROR)
logger = logging.getLogger("ExpensesMCP")
logger.setLevel(logging.INFO)

# Configure Azure SDK OpenTelemetry to use OTEL
settings.tracing_implementation = "opentelemetry"

# Configure OpenTelemetry exporters based on OPENTELEMETRY_PLATFORM env var
opentelemetry_platform = os.getenv("OPENTELEMETRY_PLATFORM", "none").lower()
if opentelemetry_platform == "appinsights" and os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    logger.info("Setting up Azure Monitor instrumentation")
    configure_azure_monitor()
elif opentelemetry_platform == "logfire" and os.getenv("LOGFIRE_TOKEN"):
    logger.info("Setting up Logfire instrumentation")
    logfire.configure(service_name="expenses-mcp", send_to_logfire=True)

# Configure Cosmos DB client
if RUNNING_IN_PRODUCTION:
    azure_credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    logger.info("Using Managed Identity Credential for Azure authentication")
else:
    azure_credential = DefaultAzureCredential()
    logger.info("Using Default Azure Credential for Azure authentication")
cosmos_client = CosmosClient(
    url=f"https://{os.environ['AZURE_COSMOSDB_ACCOUNT']}.documents.azure.com:443/",
    credential=azure_credential,
)
cosmos_db = cosmos_client.get_database_client(os.environ["AZURE_COSMOSDB_DATABASE"])
cosmos_container = cosmos_db.get_container_client(os.environ["AZURE_COSMOSDB_USER_CONTAINER"])

# Configure authentication: RemoteAuthProvider with AzureJWTVerifier
# VS Code authenticates directly with Entra (pre-registered as authorized client).
# The server validates tokens using Entra's public signing keys — no client secret needed.
base_url = os.environ.get("SERVER_BASE_URL", "http://localhost:8000")
verifier = AzureJWTVerifier(
    client_id=os.environ["ENTRA_PROXY_AZURE_CLIENT_ID"],
    tenant_id=os.environ["AZURE_TENANT_ID"],
    required_scopes=["user_impersonation"],
)
auth = RemoteAuthProvider(
    token_verifier=verifier,
    authorization_servers=[f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}/v2.0"],
    base_url=base_url,
)
logger.info(
    "Using RemoteAuthProvider for direct Entra auth (client_id=%s, tenant=%s)",
    os.environ["ENTRA_PROXY_AZURE_CLIENT_ID"],
    os.environ["AZURE_TENANT_ID"],
)

# MSAL confidential client for OBO flow (used by get_expense_stats for Graph API calls)
# In production: uses managed identity as a federated credential (no secret needed)
# Locally: uses a client secret from the local dev app registration
client_secret = os.environ.get("ENTRA_PROXY_AZURE_CLIENT_SECRET")
if RUNNING_IN_PRODUCTION:
    from msal import ManagedIdentityClient, UserAssignedManagedIdentity

    mi_client = ManagedIdentityClient(
        UserAssignedManagedIdentity(client_id=os.environ["AZURE_CLIENT_ID"]),
        token_cache=TokenCache(),
    )

    def _get_mi_assertion():
        result = mi_client.acquire_token_for_client(resource="api://AzureADTokenExchange")
        if "access_token" not in result:
            raise RuntimeError(f"Failed to get MI assertion: {result.get('error_description', 'unknown error')}")
        return result["access_token"]

    confidential_client = ConfidentialClientApplication(
        client_id=os.environ["ENTRA_PROXY_AZURE_CLIENT_ID"],
        client_credential={"client_assertion": _get_mi_assertion},
        authority=f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}",
        token_cache=TokenCache(),
    )
    logger.info("Using managed identity federated credential for OBO flow")
elif client_secret:
    confidential_client = ConfidentialClientApplication(
        client_id=os.environ["ENTRA_PROXY_AZURE_CLIENT_ID"],
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}",
        token_cache=TokenCache(),
    )
    logger.info("Using client secret for OBO flow")
else:
    confidential_client = None
    logger.warning("No credential available for OBO flow — admin group checks will be unavailable")


async def check_user_in_group(graph_token: str, group_id: str) -> bool:
    """Check if the authenticated user is a member of the specified group (including transitive membership).
    Used locally where we have a Graph token via OBO flow."""
    async with httpx.AsyncClient() as client:
        url = (
            "https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.group"
            f"?$filter=id eq '{group_id}'&$count=true"
        )
        logger.info(f"Checking group membership for group ID: {group_id}")
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {graph_token}",
                "ConsistencyLevel": "eventual",
            },
        )
        response.raise_for_status()
        data = response.json()
        membership_count = data.get("@odata.count", 0)
        logger.info(f"User membership count in group {group_id}: {membership_count}")
        return membership_count > 0


# Middleware to populate user_id in per-request context state
class UserAuthMiddleware(Middleware):
    def _get_user_id(self):
        token = get_access_token()
        if not (token and hasattr(token, "claims")):
            return None
        return token.claims.get("oid")

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        user_id = self._get_user_id()
        if context.fastmcp_context is not None:
            await context.fastmcp_context.set_state("user_id", user_id)
        return await call_next(context)

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        user_id = self._get_user_id()
        if context.fastmcp_context is not None:
            await context.fastmcp_context.set_state("user_id", user_id)
        return await call_next(context)


# Create the MCP server
mcp = FastMCP("Expenses Tracker", auth=auth, middleware=[OpenTelemetryMiddleware("ExpensesMCP"), UserAuthMiddleware()])


class PaymentMethod(Enum):
    AMEX = "amex"
    VISA = "visa"
    CASH = "cash"


class Category(Enum):
    FOOD = "food"
    TRANSPORT = "transport"
    ENTERTAINMENT = "entertainment"
    SHOPPING = "shopping"
    GADGET = "gadget"
    OTHER = "other"


@mcp.tool
async def add_user_expense(
    date: Annotated[date, "Date of the expense in YYYY-MM-DD format"],
    amount: Annotated[float, "Positive numeric amount of the expense"],
    category: Annotated[Category, "Category label"],
    description: Annotated[str, "Human-readable description of the expense"],
    payment_method: Annotated[PaymentMethod, "Payment method used"],
    ctx: Context,
):
    """Add a new expense to Cosmos DB."""
    if amount <= 0:
        return "Error: Amount must be positive"

    date_iso = date.isoformat()
    logger.info(f"Adding expense: ${amount} for {description} on {date_iso}")

    try:
        # Read user_id stored by middleware
        user_id = await ctx.get_state("user_id")
        if not user_id:
            return "Error: Authentication required (no user_id present)"
        expense_id = str(uuid.uuid4())
        expense_item = {
            "id": expense_id,
            "user_id": user_id,
            "date": date_iso,
            "amount": amount,
            "category": category.value,
            "description": description,
            "payment_method": payment_method.value,
        }
        await cosmos_container.create_item(body=expense_item)
        return f"Successfully added expense: ${amount} for {description} on {date_iso}"

    except Exception as e:
        logger.error(f"Error adding expense: {str(e)}")
        return f"Error: Unable to add expense - {str(e)}"


@mcp.tool
async def get_user_expenses(ctx: Context):
    """Get the authenticated user's expense data from Cosmos DB."""

    try:
        user_id = await ctx.get_state("user_id")
        if not user_id:
            return "Error: Authentication required (no user_id present)"
        query = "SELECT * FROM c WHERE c.user_id = @uid ORDER BY c.date DESC"
        parameters = [{"name": "@uid", "value": user_id}]
        expenses_data = []

        async for item in cosmos_container.query_items(query=query, parameters=parameters, partition_key=user_id):
            expenses_data.append(item)

        if not expenses_data:
            return "No expenses found."

        expense_summary = f"Expense data ({len(expenses_data)} entries):\n\n"
        for expense in expenses_data:
            expense_summary += (
                f"Date: {expense.get('date', 'N/A')}, "
                f"Amount: ${expense.get('amount', 0)}, "
                f"Category: {expense.get('category', 'N/A')}, "
                f"Description: {expense.get('description', 'N/A')}, "
                f"Payment: {expense.get('payment_method', 'N/A')}\n"
            )

        return expense_summary

    except Exception as e:
        logger.error(f"Error reading expenses: {str(e)}")
        return f"Error: Unable to retrieve expense data - {str(e)}"


@mcp.tool
async def get_expense_stats(ctx: Context):
    """Get a statistical summary of expenses (count per category) for all users.
    Only accessible to users in the authorized admin group.
    """
    admin_group_id = os.environ.get("ENTRA_ADMIN_GROUP_ID", "")
    if not admin_group_id:
        return "Error: Admin group ID not configured. Set ENTRA_ADMIN_GROUP_ID environment variable."

    try:
        if not confidential_client:
            return "Error: Admin group check requires a client secret (ENTRA_PROXY_AZURE_CLIENT_SECRET)."

        access_token = get_access_token()
        if not access_token:
            return "Error: Authentication required"
        graph_resource_access_token = confidential_client.acquire_token_on_behalf_of(
            user_assertion=access_token.token, scopes=["https://graph.microsoft.com/.default"]
        )
        if "error" in graph_resource_access_token:
            logger.error(
                "OBO token acquisition failed: %s",
                graph_resource_access_token.get("error_description", "Unknown error"),
            )
            return "Error: Unable to verify permissions. Please try again later."
        is_admin = await check_user_in_group(graph_resource_access_token["access_token"], admin_group_id)

        if not is_admin:
            return "Error: Unauthorized. You do not have permission to access expense statistics."

        # Query Cosmos DB for stats across all users
        # We fetch categories and aggregate in Python to avoid cross-partition GROUP BY limitations
        query = "SELECT c.category FROM c"
        stats = {}
        async for item in cosmos_container.query_items(query=query):
            category = item.get("category", "Unknown")
            stats[category] = stats.get(category, 0) + 1

        if not stats:
            return "No expense data found to summarize."

        summary = "Expense Statistics (Count per Category):\n"
        for category, count in stats.items():
            summary += f"- Category {category}: {count} expenses\n"

        return summary

    except Exception:
        logger.error("Error retrieving expense stats", exc_info=True)
        return "Error: Unable to retrieve expense statistics. Please try again later."


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request):
    """Health check endpoint for service availability."""
    return JSONResponse({"status": "healthy", "service": "mcp-server"})


# Configure Starlette middleware for OpenTelemetry
# We must do this *after* defining all the MCP server routes
app = mcp.http_app()
StarletteInstrumentor.instrument_app(app)
