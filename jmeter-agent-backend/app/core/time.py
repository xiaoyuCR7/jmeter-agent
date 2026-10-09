"""UTC clock helpers for legacy naive database and API timestamp contracts."""

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return current UTC without tzinfo until persisted fields are migrated."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
