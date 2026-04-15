import os
from datetime import datetime, timezone

def utc_timestamp():
    """Return an RFC3339 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def env_bool(name, default=False):
    """Parse boolean environment variables."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name, default):
    """Parse integer environment variables."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_first(names, default=None):
    """Return first non-empty environment variable from a list of names."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def env_int_first(names, default):
    """Parse first non-empty integer environment variable from names."""
    value = env_first(names)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool_first(names, default=False):
    """Parse first non-empty boolean environment variable from names."""
    value = env_first(names)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
