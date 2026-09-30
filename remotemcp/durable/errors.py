from __future__ import annotations


RETRY_POLICY = {
    "INVALID_ARGUMENT": "NEVER",
    "FORBIDDEN": "NEVER",
    "NOT_FOUND": "NEVER",
    "PATH_ESCAPE": "NEVER",
    "COMMAND_NOT_ALLOWED": "NEVER",
    "CAS_MISMATCH": "NEVER",
    "OPERATION_CONFLICT": "NEVER",
    "AUTH_REQUIRED": "REAUTH_ONCE_THEN_RETRY",
    "RATE_LIMITED": "READ_OR_SAME_OPERATION_ID",
    "TRANSPORT_TIMEOUT": "READ_OR_SAME_OPERATION_ID",
    "UPSTREAM_502": "READ_OR_SAME_OPERATION_ID",
    "UPSTREAM_503": "READ_OR_SAME_OPERATION_ID",
    "UPSTREAM_504": "READ_OR_SAME_OPERATION_ID",
    "DB_BUSY": "READ_OR_SAME_OPERATION_ID",
    "SPAWN_RESOURCE_EXHAUSTED_BEFORE_CHILD": "SAME_OPERATION_ID",
    "SPAWN_INVALID_EXECUTABLE": "NEVER",
    "OPERATION_IN_DOUBT": "RECONCILE_ONLY",
    "PROCESS_OWNERSHIP_MISMATCH": "RECONCILE_ONLY",
    "STARTUP_FATAL_SCHEMA_MISMATCH": "NEVER",
}


class DurableError(RuntimeError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retry_policy = RETRY_POLICY.get(code, "NEVER")

    def as_dict(self) -> dict:
        return {
            "error": self.code,
            "message": self.message,
            "retry_policy": self.retry_policy,
            "details": self.details,
        }
