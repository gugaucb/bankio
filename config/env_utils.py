"""Small, testable environment helpers for external configuration.

All values come from the process environment; sensitive settings may
alternatively point to a file (Docker Secrets pattern: *_FILE).
"""
import os


def env_str(name, default=""):
    value = os.environ.get(name, "")
    return value if value else default


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default=0):
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}")


def env_list(name, default=None):
    """Comma-separated list; empty entries are dropped."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return list(default) if default else []
    return [item.strip() for item in raw.split(",") if item.strip()]


def secret_or_file(name):
    """Return a secret from NAME, falling back to NAME_FILE contents.

    Docker Secrets mount files (typically under /run/secrets/); pointing e.g.
    POSTGRES_PASSWORD_FILE at one keeps the plaintext out of the environment.
    Raises ImproperlyConfigured-style ValueError when neither is set.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if file_path:
        with open(file_path) as fh:
            content = fh.read().strip()
        if content:
            return content
    raise ValueError(f"Required secret {name} is missing (set {name} or {name}_FILE)")
