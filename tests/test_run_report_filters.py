"""Backward-compat checks for optional run_report dimension filters.

These tests import only the pure helpers (no live GA4 / credentials).
Run: python3 tests/test_run_report_filters.py
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load_ga4_server_with_stubs():
    """Load ga4_server.py without requiring google.* / fastmcp installed."""
    for mod_name in (
        "google",
        "google.auth",
        "google.auth.transport",
        "google.auth.transport.requests",
        "google.oauth2",
        "google.oauth2.credentials",
        "google.oauth2.service_account",
        "google.analytics",
        "google.analytics.data_v1beta",
        "google.analytics.data_v1beta.types",
        "google.analytics.admin_v1beta",
        "google.analytics.admin_v1beta.types",
        "fastmcp",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    types_mod = sys.modules["google.analytics.data_v1beta.types"]

    class _MatchType:
        CONTAINS = "CONTAINS"
        EXACT = "EXACT"
        BEGINS_WITH = "BEGINS_WITH"
        ENDS_WITH = "ENDS_WITH"
        FULL_REGEXP = "FULL_REGEXP"
        PARTIAL_REGEXP = "PARTIAL_REGEXP"

    class _StringFilter:
        MatchType = _MatchType

        def __init__(self, match_type=None, value=None):
            self.match_type = match_type
            self.value = value

    class _Filter:
        StringFilter = _StringFilter

        def __init__(self, field_name=None, string_filter=None, empty_filter=None):
            self.field_name = field_name
            self.string_filter = string_filter

    class _FilterExpression:
        def __init__(self, filter=None, not_expression=None, and_group=None):
            self.filter = filter
            self.not_expression = not_expression
            self.and_group = and_group

    class _FilterExpressionList:
        def __init__(self, expressions=None):
            self.expressions = expressions or []

    types_mod.DateRange = MagicMock()
    types_mod.Dimension = MagicMock()
    types_mod.Filter = _Filter
    types_mod.FilterExpression = _FilterExpression
    types_mod.FilterExpressionList = _FilterExpressionList
    types_mod.Metric = MagicMock()
    types_mod.RunReportRequest = MagicMock()
    types_mod.RunRealtimeReportRequest = MagicMock()
    types_mod.OrderBy = MagicMock()

    admin_types = sys.modules["google.analytics.admin_v1beta.types"]
    admin_types.DataStream = MagicMock()

    admin_mod = sys.modules["google.analytics.admin_v1beta"]
    admin_mod.AnalyticsAdminServiceClient = MagicMock()

    data_mod = sys.modules["google.analytics.data_v1beta"]
    data_mod.BetaAnalyticsDataClient = MagicMock()

    oauth_creds = sys.modules["google.oauth2.credentials"]
    oauth_creds.Credentials = MagicMock()
    sa = sys.modules["google.oauth2.service_account"]
    sa.Credentials = MagicMock()

    req = sys.modules["google.auth.transport.requests"]
    req.Request = MagicMock()

    fm = sys.modules["fastmcp"]

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def deco(fn):
                return fn

            # Support @mcp.tool and @mcp.tool()
            if args and callable(args[0]) and not kwargs:
                return args[0]
            return deco

        def add_middleware(self, *args, **kwargs):
            return None

        def run(self, *args, **kwargs):
            return None

    fm.FastMCP = MagicMock(return_value=_FakeMCP())

    path = ROOT / "ga4_server.py"
    spec = importlib.util.spec_from_file_location("ga4_server_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ga4 = _load_ga4_server_with_stubs()

    assert ga4._dimension_filter_expr(None, "contains", None) is None
    assert ga4._dimension_filter_expr("", "contains", "") is None
    assert ga4._dimension_filter_expr("landingPage", "contains", "") is None
    assert ga4._and_dimension_filters(None, None) is None

    f = ga4._dimension_filter_expr("landingPage", "contains", "/homme")
    assert f is not None
    assert f.filter.field_name == "landingPage"
    assert f.filter.string_filter.value == "/homme"
    assert f.filter.string_filter.match_type == "CONTAINS"

    a = ga4._dimension_filter_expr(
        "sessionDefaultChannelGroup", "equals", "Organic Search"
    )
    b = ga4._dimension_filter_expr("landingPage", "contains", "femme")
    combined = ga4._and_dimension_filters(a, b)
    assert combined is not None
    assert len(combined.and_group.expressions) == 2

    sig = inspect.signature(ga4.run_report)
    params = sig.parameters
    assert params["dimensions"].default == "date"
    assert params["metrics"].default == "activeUsers"
    assert params["start_date"].default == "28daysAgo"
    assert params["end_date"].default == "today"
    assert params["row_limit"].default == 100
    assert params["order_by_metric"].default is None
    assert params["descending"].default is True
    assert params["filter_dimension"].default is None
    assert params["filter_expression"].default is None
    assert params["filter2_dimension"].default is None
    assert params["filter2_expression"].default is None
    assert params["property_id"].default is inspect.Parameter.empty

    print("OK — run_report filters are additive / backward compatible")


if __name__ == "__main__":
    main()
