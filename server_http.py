# server_http.py
import os, json, logging
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

import ga4_server

logger = logging.getLogger(__name__)

# ==== Config (env-driven) ====
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

MCP_ENABLE_OAUTH21 = os.getenv("MCP_ENABLE_OAUTH21", "").lower() in ("1", "true", "yes")

# Legacy OAuth config (used when OAuth21 is disabled)
CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GA4_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GA4_OAUTH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or os.getenv("GA4_OAUTH_REDIRECT_URI")

CREDENTIALS_DIR = os.getenv("GOOGLE_MCP_CREDENTIALS_DIR") or os.getenv("GA4_MCP_CREDENTIALS_DIR") or "/data"
TOKEN_PATH = os.getenv("GA4_OAUTH_TOKEN_PATH") or os.path.join(CREDENTIALS_DIR, "ga4_token.json")

CLIENT_SECRETS = os.getenv("GA4_OAUTH_CLIENT_SECRETS_FILE", "/etc/secrets/client_secrets.json")

# ---- Bearer auth middleware (used when OAuth21 is NOT enabled) ----
class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/oauth2/") or path.startswith("/.well-known/"):
            return await call_next(request)
        required = os.getenv("MCP_BEARER_TOKEN")
        if required:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != required:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

# ---- Legacy OAuth helpers (only used when MCP_ENABLE_OAUTH21 is false) ----
_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _flow():
    from google_auth_oauthlib.flow import Flow
    if CLIENT_ID and CLIENT_SECRET and REDIRECT_URI:
        client_config = {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": _GOOGLE_AUTH_URI,
                "token_uri": _GOOGLE_TOKEN_URI,
                "redirect_uris": [REDIRECT_URI],
                "javascript_origins": [],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )
    return Flow.from_client_secrets_file(
        CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )


def _validate_oauth_env():
    if not REDIRECT_URI:
        return "GOOGLE_OAUTH_REDIRECT_URI (or GA4_OAUTH_REDIRECT_URI) is not set."
    if CLIENT_ID and CLIENT_SECRET:
        return None
    if os.path.exists(CLIENT_SECRETS):
        return None
    return (
        "No OAuth client configured. Provide GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (or GA4_* vars) "
        "or mount a client secrets file and set GA4_OAUTH_CLIENT_SECRETS_FILE."
    )


async def oauth_authorize(request: Request):
    err = _validate_oauth_env()
    if err:
        return JSONResponse({"error": err}, status_code=500)
    try:
        flow = _flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes=False,
            prompt="consent",
        )
        return JSONResponse({"auth_url": auth_url, "state": state})
    except Exception as e:
        return JSONResponse({"error": f"oauth_authorize failed: {str(e)}"}, status_code=500)


async def oauth_start(request: Request):
    err = _validate_oauth_env()
    if err:
        return JSONResponse({"error": err}, status_code=500)
    try:
        flow = _flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes=False,
            prompt="consent",
        )
        return RedirectResponse(auth_url)
    except Exception as e:
        return JSONResponse({"error": f"oauth_start failed: {str(e)}"}, status_code=500)


async def oauth_callback(request: Request):
    err = _validate_oauth_env()
    if err:
        return JSONResponse({"error": err}, status_code=500)
    try:
        flow = _flow()
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        return JSONResponse({"status": "ok", "token_path": TOKEN_PATH})
    except Exception as e:
        return JSONResponse({"error": f"oauth_callback failed: {str(e)}", "token_path": TOKEN_PATH}, status_code=500)


async def oauth_exchange(request: Request):
    err = _validate_oauth_env()
    if err:
        return JSONResponse({"error": err}, status_code=500)
    try:
        authorization_response = request.query_params.get("authorization_response")
        if not authorization_response and request.method == "POST":
            try:
                body = await request.json()
                authorization_response = body.get("authorization_response")
            except Exception:
                authorization_response = None
        if not authorization_response:
            return JSONResponse({"error": "authorization_response is required"}, status_code=400)

        flow = _flow()
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        return JSONResponse({"status": "ok", "token_path": TOKEN_PATH})
    except Exception as e:
        return JSONResponse({"error": f"oauth_exchange failed: {str(e)}", "token_path": TOKEN_PATH}, status_code=500)


# ---- Wire the MCP server ----
try:
    mcp = getattr(ga4_server, "mcp")
except AttributeError as e:
    raise RuntimeError(
        "Expected ga4_server.py to expose a FastMCP instance named `mcp`."
    ) from e

# ---- Configure OAuth 2.1 auth provider when enabled ----
_auth_provider = None

if MCP_ENABLE_OAUTH21:
    try:
        from auth.oauth_config import get_oauth_config
        from auth.google_oauth_provider import GoogleOAuthProvider

        config = get_oauth_config()
        base_url = config.get_oauth_base_url()

        if not config.is_configured():
            logger.warning("OAuth 2.1 enabled but GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET not configured")
        else:
            _auth_provider = GoogleOAuthProvider(base_url=base_url)
            mcp.auth = _auth_provider
            logger.info("OAuth 2.1 per-user authentication enabled (OAuthProvider)")
    except Exception as e:
        logger.error(f"Failed to initialize OAuth 2.1 auth: {e}", exc_info=True)
else:
    logger.info("OAuth 2.1 disabled - using legacy authentication mode")


# ---- Build routes and app ----
async def _protected_resource_metadata(request: Request):
    """Advertise the MCP endpoint (/mcp) as the canonical protected resource.

    FastMCP's built-in Protected Resource Metadata advertises the server *root*
    (e.g. https://host/). Strict MCP clients (per the MCP authorization spec)
    require the metadata `resource` to equal the server URL they connect to
    (https://host/mcp); when it doesn't match they treat the token as not
    applicable and never send it, so /mcp returns 401 even after a successful
    login. Serving this at the origin .well-known path (where the client and
    the WWW-Authenticate header point) with the /mcp resource fixes that.
    """
    from auth.oauth_config import get_oauth_config
    base = get_oauth_config().get_oauth_base_url().rstrip("/")
    return JSONResponse(
        {
            "resource": f"{base}/mcp",
            "authorization_servers": [f"{base}/"],
            "scopes_supported": SCOPES,
            "bearer_methods_supported": ["header"],
        },
        headers={"Cache-Control": "no-store"},
    )


def _create_app():
    """Build the Starlette app with appropriate routes and middleware."""
    mcp_app = mcp.http_app()

    if MCP_ENABLE_OAUTH21 and _auth_provider:
        # Override the Protected Resource Metadata so it advertises /mcp as the
        # resource (see handler docstring). Inserting first gives it precedence
        # over FastMCP's built-in root-resource route at the same path.
        mcp_app.router.routes.insert(
            0,
            Route(
                "/.well-known/oauth-protected-resource",
                _protected_resource_metadata,
                methods=["GET"],
            ),
        )
        return mcp_app

    if MCP_ENABLE_OAUTH21 and not _auth_provider:
        logger.warning(
            "SECURITY: MCP_ENABLE_OAUTH21 was requested but the OAuth 2.1 provider "
            "failed to initialize (missing GOOGLE_OAUTH_CLIENT_ID/SECRET?). "
            "Falling back to legacy mode - per-user login is NOT active."
        )

    if not os.getenv("MCP_BEARER_TOKEN"):
        logger.warning(
            "SECURITY: /mcp is being served WITHOUT authentication (OAuth 2.1 is off "
            "and MCP_BEARER_TOKEN is unset). Anyone who can reach this URL can call "
            "your GA4 tools using the server's single identity. For a remote/multi-user "
            "deployment set MCP_ENABLE_OAUTH21=true; for a trusted backend set "
            "MCP_BEARER_TOKEN to a long random secret."
        )

    routes = [
        Route("/oauth2/authorize", oauth_authorize),
        Route("/oauth2/start", oauth_start),
        Route("/oauth2/callback", oauth_callback),
        Route("/oauth2/exchange", oauth_exchange),
        Mount("/", app=mcp_app),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(BearerAuthMiddleware)],
        lifespan=mcp_app.lifespan,
    )


app = _create_app()
