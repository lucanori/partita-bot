ADMIN_OPERATION_PREFIX = "ADMIN_OPERATION:"
RECHECK_BLOCKED_USERS = "RECHECK_BLOCKED_USERS"
CLEANUP_USERS = "CLEANUP_USERS"
DELETE_SENT_LAST_HOURS = "DELETE_SENT_LAST_HOURS"


def format_admin_operation(operation: str, *params: str) -> str:
    if params:
        param_str = ":".join(params)
        return f"{ADMIN_OPERATION_PREFIX}{operation}:{param_str}"
    return f"{ADMIN_OPERATION_PREFIX}{operation}"
