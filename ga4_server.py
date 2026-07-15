from typing import Any, Optional, List, Dict, Tuple
import os
import re
import json
import time
import difflib
import logging
import asyncio
from urllib.parse import urlparse

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    RunRealtimeReportRequest,
    OrderBy,
)
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.admin_v1beta.types import DataStream

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("ga4-server")

# Per-user OAuth middleware (only active on the HTTP transport).
try:
    from auth.auth_info_middleware import AuthInfoMiddleware
    mcp.add_middleware(AuthInfoMiddleware())
    logger.info("AuthInfoMiddleware added to MCP server")
except (ImportError, AttributeError) as e:
    logger.debug(f"Auth middleware not available (stdio-only mode): {e}")

# GA4 read-only scope.
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Optional service-account file (legacy / single-identity mode).
GA4_CREDENTIALS_PATH = os.environ.get("GA4_CREDENTIALS_PATH")
POSSIBLE_CREDENTIAL_PATHS = [
    GA4_CREDENTIALS_PATH,
    os.path.join(SCRIPT_DIR, "service_account_credentials.json"),
    os.path.join(os.getcwd(), "service_account_credentials.json"),
]

# Optional pre-provisioned single-user OAuth token (stdio mode).
DEFAULT_CREDENTIALS_DIR = (
    os.getenv("GOOGLE_MCP_CREDENTIALS_DIR")
    or os.getenv("GA4_MCP_CREDENTIALS_DIR")
    or "/data"
)
OAUTH_TOKEN_PATH = os.getenv(
    "GA4_OAUTH_TOKEN_PATH", os.path.join(DEFAULT_CREDENTIALS_DIR, "ga4_token.json")
)

SKIP_OAUTH = os.environ.get("GA4_SKIP_OAUTH", "").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Credential resolution (identical strategy to gsc_server.py)
# ---------------------------------------------------------------------------

def _get_authenticated_user_email() -> Optional[str]:
    """Per-user email injected by AuthInfoMiddleware (HTTP/OAuth 2.1 mode)."""
    try:
        from fastmcp.server.dependencies import get_context
        ctx = get_context()
        if ctx:
            return ctx.get_state("authenticated_user_email")
    except Exception:
        pass
    return None


def get_credentials_for_user(user_email: str) -> Credentials:
    """Resolve a user's Google credentials: in-memory session first, then disk."""
    from auth.oauth21_session_store import get_oauth21_session_store
    from auth.credential_store import get_credential_store

    store = get_oauth21_session_store()
    creds = store.get_credentials(user_email)
    if creds:
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            return creds

    cred_store = get_credential_store()
    creds = cred_store.get_credential(user_email)
    if creds:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            cred_store.store_credential(user_email, creds)
        return creds

    raise ValueError(
        f"No credentials found for user {user_email}. Please authenticate via OAuth first."
    )


def _load_single_user_credentials() -> Optional[Credentials]:
    """Legacy/stdio: a single OAuth token on disk, or a service account."""
    if not SKIP_OAUTH and os.path.exists(OAUTH_TOKEN_PATH):
        with open(OAUTH_TOKEN_PATH) as f:
            data = json.load(f)
        return Credentials.from_authorized_user_info(data, scopes=SCOPES)

    for cred_path in POSSIBLE_CREDENTIAL_PATHS:
        if cred_path and os.path.exists(cred_path):
            return service_account.Credentials.from_service_account_file(
                cred_path, scopes=SCOPES
            )
    return None


def _resolve_credentials(user_email: Optional[str]) -> Credentials:
    """Per-user creds when available; otherwise the single-user fallback."""
    if user_email:
        try:
            return get_credentials_for_user(user_email)
        except Exception as e:
            logger.debug(f"Per-user auth failed for {user_email}: {e}")

    creds = _load_single_user_credentials()
    if creds is None:
        raise FileNotFoundError(
            "Authentication failed. Provide per-user OAuth (HTTP mode), a pre-provisioned "
            f"OAuth token at {OAUTH_TOKEN_PATH}, or a service account credentials file."
        )
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return creds


# GA4 gRPC clients are thread-safe, so (unlike GSC/httplib2) we can cache one per user.
_data_clients: Dict[str, BetaAnalyticsDataClient] = {}
_admin_clients: Dict[str, AnalyticsAdminServiceClient] = {}


def get_data_client(user_email: Optional[str]) -> BetaAnalyticsDataClient:
    key = user_email or "__default__"
    if key not in _data_clients:
        creds = _resolve_credentials(user_email)
        _data_clients[key] = BetaAnalyticsDataClient(credentials=creds)
    return _data_clients[key]


def get_admin_client(user_email: Optional[str]) -> AnalyticsAdminServiceClient:
    key = user_email or "__default__"
    if key not in _admin_clients:
        creds = _resolve_credentials(user_email)
        _admin_clients[key] = AnalyticsAdminServiceClient(credentials=creds)
    return _admin_clients[key]


def _normalize_property(property_id: str) -> str:
    """Accept '123456', 'properties/123456', or a full resource name."""
    pid = str(property_id).strip()
    if pid.startswith("properties/"):
        return pid
    return f"properties/{pid}"


# ---------------------------------------------------------------------------
# Domain -> property discovery
# ---------------------------------------------------------------------------

def _normalize_host(value: str) -> str:
    """Reduce a URL or domain to a bare, comparable host.

    'https://www.Example.com/path?x=1' -> 'example.com'
    'Example.com'                       -> 'example.com'
    """
    if not value:
        return ""
    raw = str(value).strip().lower()
    # urlparse needs a scheme to populate netloc; add one if missing.
    parsed = urlparse(raw if re.match(r"^[a-z][a-z0-9+.-]*://", raw) else f"http://{raw}")
    host = parsed.hostname or ""
    if not host:
        # Fallback: pull the first domain-looking token out of the string.
        m = re.search(r"([a-z0-9][a-z0-9-]*\.)+[a-z]{2,}", raw)
        host = m.group(0) if m else raw
    host = host.lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


# Per-user cache of the property/data-stream index: {user_key: (built_at, index)}.
_property_index_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_PROPERTY_INDEX_TTL = int(os.getenv("GA4_PROPERTY_INDEX_TTL", "900"))


def _build_property_index(user_email: Optional[str]) -> List[Dict[str, Any]]:
    """Build a flat index of every GA4 property the user can see, annotated with
    the website URL(s) of each property's web data stream(s).

    One property may have several web streams (several URLs), so each stream URL
    is folded into the property's ``web_hosts`` list for domain matching.
    """
    admin = get_admin_client(user_email)
    index: List[Dict[str, Any]] = []

    for acct in admin.list_account_summaries():
        for prop in acct.property_summaries:
            parent = prop.property  # "properties/123456789"
            web_uris: List[str] = []
            try:
                for stream in admin.list_data_streams(parent=parent):
                    if stream.type_ == DataStream.DataStreamType.WEB_DATA_STREAM:
                        uri = stream.web_stream_data.default_uri
                        if uri:
                            web_uris.append(uri)
            except Exception as e:
                # A single inaccessible property must not sink the whole index.
                logger.debug("list_data_streams failed for %s: %s", parent, e)

            index.append(
                {
                    "account": acct.account,
                    "account_display_name": acct.display_name,
                    "property": parent,
                    "property_id": parent.split("/")[-1],
                    "property_display_name": prop.display_name,
                    "web_uris": web_uris,
                    "web_hosts": [_normalize_host(u) for u in web_uris],
                }
            )
    return index


def _get_property_index(
    user_email: Optional[str], *, force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """Return the cached property index for a user, rebuilding on TTL expiry."""
    key = user_email or "__default__"
    now = time.time()
    cached = _property_index_cache.get(key)
    if cached and not force_refresh and (now - cached[0]) < _PROPERTY_INDEX_TTL:
        return cached[1]
    index = _build_property_index(user_email)
    _property_index_cache[key] = (now, index)
    return index


def _score_property(q_host: str, q_raw: str, entry: Dict[str, Any]) -> float:
    """Relevance score for a property against a domain/URL/name query.

    Higher is better. Exact host and exact property-id matches dominate; suffix
    and substring host matches come next; name similarity is the weak fallback.
    """
    score = 0.0

    if q_raw and q_raw == entry["property_id"]:
        return 1000.0

    for host in entry["web_hosts"]:
        if not host:
            continue
        if q_host and q_host == host:
            score = max(score, 100.0)
        elif q_host and (host.endswith("." + q_host) or q_host.endswith("." + host)):
            score = max(score, 70.0)
        elif q_host and (q_host in host or host in q_host):
            ratio = difflib.SequenceMatcher(None, q_host, host).ratio()
            score = max(score, 40.0 + ratio)
        else:
            base = difflib.SequenceMatcher(None, q_host or q_raw, host).ratio()
            score = max(score, base)

    for name in (entry["property_display_name"], entry["account_display_name"]):
        low = (name or "").lower()
        if not low:
            continue
        if q_raw and q_raw in low:
            score = max(score, 20.0 + (len(q_raw) / max(len(low), 1)))
        else:
            score = max(score, difflib.SequenceMatcher(None, q_raw, low).ratio())

    return score


def _render_report(
    dimension_headers: List[str],
    metric_headers: List[str],
    response: Any,
    *,
    empty_msg: str,
) -> str:
    """Render a GA4 report response as a readable, self-describing table."""
    header = dimension_headers + metric_headers
    n_returned = len(response.rows)
    if n_returned == 0:
        return empty_msg

    header_line = " | ".join(header)
    separator = "-" * max(len(header_line), 20)
    lines: List[str] = [header_line, separator]
    for row in response.rows:
        values = [dv.value for dv in row.dimension_values] + [
            mv.value for mv in row.metric_values
        ]
        lines.append(" | ".join(values))

    # Totals row (present on core reports, absent on realtime). Guard so a
    # formatting problem here never masks the actual data above.
    try:
        totals = list(getattr(response, "totals", []) or [])
        if totals:
            t = totals[0]
            total_values = [mv.value for mv in t.metric_values]
            if dimension_headers:
                label_row = ["TOTALS"] + [""] * (len(dimension_headers) - 1)
            else:
                label_row = []
            label_row += total_values
            lines.append(separator)
            lines.append(" | ".join(label_row))
    except Exception:
        pass

    summary_bits = [f"{n_returned} row(s) returned"]
    row_count = getattr(response, "row_count", 0) or 0
    if row_count > n_returned:
        summary_bits.append(
            f"{row_count} total matching rows (raise row_limit to see more)"
        )
    return "\n".join(lines) + "\n\n" + "; ".join(summary_bits) + "."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_account_summaries() -> str:
    """List all Google Analytics accounts and their GA4 properties the user can access."""
    try:
        client = get_admin_client(_get_authenticated_user_email())
        results = await asyncio.to_thread(lambda: list(client.list_account_summaries()))
        if not results:
            return "No Google Analytics accounts found for this user."
        lines: List[str] = []
        for acct in results:
            lines.append(f"Account: {acct.display_name} ({acct.account})")
            for prop in acct.property_summaries:
                lines.append(
                    f"  - {prop.display_name} "
                    f"(id: {prop.property.split('/')[-1]}, {prop.property})"
                )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving account summaries: {e}"


@mcp.tool()
async def find_property_by_domain(
    domain: str,
    top_k: int = 5,
    force_refresh: bool = False,
) -> str:
    """Find the GA4 property (and its numeric id) that tracks a given website.

    Use this when you know a site's domain or URL but not its GA4 property id —
    e.g. "which property is webloom.fr?". It scans every property the user can
    access, reads each property's web data stream URL, and ranks them against
    the query. Feed the returned ``property_id`` into ``run_report``,
    ``get_property_details``, etc.

    Args:
        domain: A domain, host, or full URL. Accepts "webloom.fr",
            "www.webloom.fr", or "https://www.webloom.fr/pricing". A numeric
            GA4 property id is also accepted and returns that property directly.
        top_k: Maximum number of ranked matches to return (default 5).
        force_refresh: Rebuild the property index instead of using the cache
            (the index is cached per user for ~15 minutes).

    Returns:
        JSON with the ranked ``matches`` (each has property_id, display names,
        the matched website URL(s), and a relevance score). The top match is
        usually the property you want.
    """
    try:
        user_email = _get_authenticated_user_email()
        index = await asyncio.to_thread(
            lambda: _get_property_index(user_email, force_refresh=force_refresh)
        )
        if not index:
            return "No GA4 properties found for this user."

        q_raw = (domain or "").strip().lower()
        if not q_raw:
            return "Error: provide a domain, URL, or property id to search for."

        # Fast path: caller already gave a numeric property id.
        digits = re.sub(r"\D", "", q_raw)
        if digits and digits == q_raw:
            direct = [e for e in index if e["property_id"] == digits]
            if direct:
                return json.dumps({"query": domain, "matches": direct[:top_k]}, indent=2)

        q_host = _normalize_host(q_raw)
        scored = [(_score_property(q_host, q_raw, e), e) for e in index]
        scored = [(s, e) for s, e in scored if s > 0.3]
        scored.sort(key=lambda x: -x[0])

        if not scored:
            return json.dumps(
                {
                    "query": domain,
                    "matches": [],
                    "hint": (
                        "No property matched. The site may not have a web data "
                        "stream, or you may lack access. Try get_account_summaries "
                        "to list everything you can see."
                    ),
                    "total_indexed": len(index),
                },
                indent=2,
            )

        matches = [
            {**e, "score": round(s, 3)} for (s, e) in scored[: max(1, int(top_k))]
        ]
        return json.dumps(
            {"query": domain, "total_indexed": len(index), "matches": matches},
            indent=2,
        )
    except Exception as e:
        return f"Error finding property by domain: {e}"


@mcp.tool()
async def get_property_details(property_id: str) -> str:
    """Return details about a GA4 property.

    Args:
        property_id: GA4 property id, e.g. "123456789" or "properties/123456789".
    """
    try:
        client = get_admin_client(_get_authenticated_user_email())
        name = _normalize_property(property_id)
        prop = await asyncio.to_thread(lambda: client.get_property(name=name))
        return json.dumps(
            {
                "name": prop.name,
                "display_name": prop.display_name,
                "time_zone": prop.time_zone,
                "currency_code": prop.currency_code,
                "industry_category": str(prop.industry_category),
                "create_time": prop.create_time.isoformat() if prop.create_time else None,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error retrieving property details: {e}"


@mcp.tool()
async def list_google_ads_links(property_id: str) -> str:
    """List Google Ads links for a GA4 property.

    Args:
        property_id: GA4 property id, e.g. "123456789".
    """
    try:
        client = get_admin_client(_get_authenticated_user_email())
        parent = _normalize_property(property_id)
        links = await asyncio.to_thread(
            lambda: list(client.list_google_ads_links(parent=parent))
        )
        if not links:
            return f"No Google Ads links found for {parent}."
        return "\n".join(
            f"- {l.name} (customer_id: {l.customer_id})" for l in links
        )
    except Exception as e:
        return f"Error listing Google Ads links: {e}"


@mcp.tool()
async def get_custom_dimensions_and_metrics(property_id: str) -> str:
    """List custom dimensions and custom metrics for a GA4 property.

    Args:
        property_id: GA4 property id, e.g. "123456789".
    """
    try:
        client = get_admin_client(_get_authenticated_user_email())
        parent = _normalize_property(property_id)
        dims = await asyncio.to_thread(
            lambda: list(client.list_custom_dimensions(parent=parent))
        )
        mets = await asyncio.to_thread(
            lambda: list(client.list_custom_metrics(parent=parent))
        )
        out = {
            "custom_dimensions": [
                {"parameter_name": d.parameter_name, "display_name": d.display_name,
                 "scope": str(d.scope)}
                for d in dims
            ],
            "custom_metrics": [
                {"parameter_name": m.parameter_name, "display_name": m.display_name,
                 "measurement_unit": str(m.measurement_unit)}
                for m in mets
            ],
        }
        return json.dumps(out, indent=2)
    except Exception as e:
        return f"Error retrieving custom dimensions/metrics: {e}"


@mcp.tool()
async def run_report(
    property_id: str,
    dimensions: str = "date",
    metrics: str = "activeUsers",
    start_date: str = "28daysAgo",
    end_date: str = "today",
    row_limit: int = 100,
    order_by_metric: Optional[str] = None,
    descending: bool = True,
) -> str:
    """Run a core GA4 report via the Data API. This is the primary,
    general-purpose analytics tool: you pick dimensions (how to break the data
    down) and metrics (what to measure) over a date range.

    If you only have a website domain and not the numeric property id, call
    ``find_property_by_domain`` first, then pass the resulting ``property_id`` here.

    Args:
        property_id: GA4 property id, e.g. "123456789" or "properties/123456789".
        dimensions: Comma-separated GA4 dimension names (how to slice the data).
            Common: date, dateHour, yearMonth, country, city, region, language,
            deviceCategory, browser, operatingSystem, platform,
            sessionDefaultChannelGroup, sessionSource, sessionMedium,
            sessionCampaignName, firstUserDefaultChannelGroup, pagePath,
            pageTitle, landingPage, hostName, eventName, itemName, itemCategory,
            newVsReturning, audienceName. Use "" for no breakdown (totals only).
        metrics: Comma-separated GA4 metric names (what to measure). Common:
            activeUsers, newUsers, totalUsers, sessions, engagedSessions,
            engagementRate, averageSessionDuration, screenPageViews,
            screenPageViewsPerSession, bounceRate, eventCount, conversions,
            userEngagementDuration, purchaseRevenue, totalRevenue,
            transactions, averagePurchaseRevenue, itemsPurchased,
            cartToViewRate. At least one metric is required.
        start_date: Start of the range. Formats: "YYYY-MM-DD" (e.g. "2026-01-01"),
            "NdaysAgo" (e.g. "28daysAgo", "7daysAgo"), "today", "yesterday".
        end_date: End of the range (same formats as start_date).
        row_limit: Max rows to return (default 100; raise for long breakdowns).
        order_by_metric: Optional metric name to sort rows by (e.g. "sessions").
            Great for "top N" questions. Leave unset to keep GA4's default order.
        descending: Sort direction when order_by_metric is set (default True =
            highest first).

    Examples:
        - Traffic trend (last 28 days, daily):
          dimensions="date", metrics="activeUsers,sessions"
        - Top channels by sessions:
          dimensions="sessionDefaultChannelGroup", metrics="sessions,engagedSessions",
          order_by_metric="sessions"
        - Top landing pages last 7 days:
          dimensions="landingPage", metrics="sessions,bounceRate",
          start_date="7daysAgo", order_by_metric="sessions", row_limit=25
        - Users by country and device:
          dimensions="country,deviceCategory", metrics="activeUsers"
        - Revenue by month:
          dimensions="yearMonth", metrics="totalRevenue,transactions"
        - Grand totals only (no breakdown):
          dimensions="", metrics="activeUsers,sessions,conversions"
        - Top events:
          dimensions="eventName", metrics="eventCount", order_by_metric="eventCount"

    Notes:
        - Dimension and metric names are case-sensitive GA4 API names (activeUsers,
          not "Active Users"). See get_custom_dimensions_and_metrics for a
          property's custom fields (use them as "customEvent:<name>" etc.).
        - For live/last-30-minutes data use run_realtime_report instead.
    """
    try:
        user_email = _get_authenticated_user_email()
        client = get_data_client(user_email)

        dimension_list = [d.strip() for d in dimensions.split(",") if d.strip()]
        metric_list = [m.strip() for m in metrics.split(",") if m.strip()]
        if not metric_list:
            return "Error running report: at least one metric is required (e.g. 'activeUsers')."

        order_bys = None
        if order_by_metric:
            order_bys = [
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name=order_by_metric),
                    desc=descending,
                )
            ]

        request = RunReportRequest(
            property=_normalize_property(property_id),
            dimensions=[Dimension(name=d) for d in dimension_list],
            metrics=[Metric(name=m) for m in metric_list],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            limit=int(row_limit),
            order_bys=order_bys,
        )
        response = await asyncio.to_thread(lambda: client.run_report(request))
        return _render_report(
            dimension_list,
            metric_list,
            response,
            empty_msg=f"No data for {property_id} in {start_date}..{end_date}.",
        )
    except Exception as e:
        return f"Error running report: {e}"


@mcp.tool()
async def run_realtime_report(
    property_id: str,
    dimensions: str = "country",
    metrics: str = "activeUsers",
    row_limit: int = 100,
) -> str:
    """Run a GA4 realtime report via the Data API.

    Args:
        property_id: GA4 property id, e.g. "123456789".
        dimensions: Comma-separated realtime dimension names.
        metrics: Comma-separated realtime metric names.
        row_limit: Max rows to return.
    """
    try:
        client = get_data_client(_get_authenticated_user_email())
        dimension_list = [d.strip() for d in dimensions.split(",") if d.strip()]
        metric_list = [m.strip() for m in metrics.split(",") if m.strip()]
        if not metric_list:
            return "Error running realtime report: at least one metric is required (e.g. 'activeUsers')."
        request = RunRealtimeReportRequest(
            property=_normalize_property(property_id),
            dimensions=[Dimension(name=d) for d in dimension_list],
            metrics=[Metric(name=m) for m in metric_list],
            limit=int(row_limit),
        )
        response = await asyncio.to_thread(lambda: client.run_realtime_report(request))
        return _render_report(
            dimension_list,
            metric_list,
            response,
            empty_msg=f"No realtime data for {property_id}.",
        )
    except Exception as e:
        return f"Error running realtime report: {e}"


if __name__ == "__main__":
    # stdio transport (local, single user). HTTP transport is served by server_http.py.
    mcp.run()
