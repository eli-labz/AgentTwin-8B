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
