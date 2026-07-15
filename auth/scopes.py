"""Google Analytics 4 OAuth scopes."""

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def get_current_scopes():
    """Return the scopes required for Google Analytics 4 API access."""
    return SCOPES
