"""Enterprise domain errors."""

from __future__ import annotations


class EnterpriseError(Exception):
    """Base class for enterprise domain failures."""


class EnterpriseSchemaError(EnterpriseError, ValueError):
    """Raised when an enterprise or persona schema payload is invalid."""


class CrossTenantAccessError(EnterpriseError, PermissionError):
    """Raised when a caller asks for a record owned by another tenant.

    Repositories raise this instead of returning another tenant's data.
    Callers must not treat it as a generic not-found.
    """


class EntityNotFoundError(EnterpriseError, KeyError):
    """Raised when a tenant-scoped lookup misses inside that tenant."""


class PolicyDeniedError(EnterpriseError, PermissionError):
    """Raised when the policy gateway returns :attr:`PolicyDecision.DENY`."""

    def __init__(
        self,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        decision: str = "DENY",
    ) -> None:
        self.reasons = reasons
        self.decision = decision
        super().__init__(message)


class ApprovalRequiredError(EnterpriseError, PermissionError):
    """Raised when a consequential action needs a human gate before proceeding."""

    def __init__(
        self,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        decision: str = "ALLOW_WITH_APPROVAL",
    ) -> None:
        self.reasons = reasons
        self.decision = decision
        super().__init__(message)


class WorkerNotAvailableError(EnterpriseError):
    """Raised when a named worker kind has no wired adapter."""

    def __init__(self, kind: str, message: str | None = None) -> None:
        self.kind = kind
        super().__init__(message or f"{kind} worker is not wired; use local")
