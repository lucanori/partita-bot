"""Shared helpers/constants for admin queue operations."""

ADMIN_OPERATION_PREFIX = "ADMIN_OPERATION:"
RECHECK_BLOCKED_USERS = "RECHECK_BLOCKED_USERS"
CLEANUP_USERS = "CLEANUP_USERS"


def format_admin_operation(operation: str) -> str:
    return f"{ADMIN_OPERATION_PREFIX}{operation}"
