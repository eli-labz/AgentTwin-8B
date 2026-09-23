"""Audit stream integrity and secret hygiene."""

from __future__ import annotations

import json
import logging

from matraix.enterprise.audit import AuditCategory, AuditLog, AuditOutcome
from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.domain import PrincipalKind, RoleName
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.secrets import RedactingFilter, StaticSecretProvider, redact, sensitive_env_names
from matraix.enterprise.sqlite_store import SqliteEnterpriseStore
from matraix.enterprise.store import create_tenant_with_default_org


def _principal(tenant: str) -> Principal:
    return Principal(subject="ops", kind=PrincipalKind.USER, tenant_id=tenant, roles=frozenset({RoleName.OPERATOR}))


def test_audit_chain_verifies_and_detects_tampering() -> None:
    store = SqliteEnterpriseStore(":memory:")
    tenant, _ = create_tenant_with_default_org(store, name="A", slug="a")
    log = AuditLog(store)
    for index in range(5):
        log.record(
            tenant_id=tenant.id.value,
            principal=_principal(tenant.id.value),
            action=f"action-{index}",
            category=AuditCategory.RESOURCE_MUTATE,
            resource_type="population",
            resource_id=f"pop_{index}",
        )
    assert log.verify_chain(tenant.id.value) == {"ok": True, "checked": 5, "broken_at_seq": None}
    # Tamper with a stored row behind the store's back.
    store._conn.execute(  # noqa: SLF001 - simulate an attacker with DB access
        "UPDATE audit_events SET action = 'forged' WHERE tenant_id = ? AND seq = 3",
        (tenant.id.value,),
    )
    store._conn.commit()  # noqa: SLF001
    result = log.verify_chain(tenant.id.value)
    assert result["ok"] is False and result["broken_at_seq"] == 3
    store.close()


def test_audit_has_no_update_or_delete_surface() -> None:
    for store in (InMemoryEnterpriseStore(), SqliteEnterpriseStore(":memory:")):
        names = {name for name in dir(store) if "audit" in name.lower() and not name.startswith("_")}
        assert names == {"append_audit", "last_audit", "list_audit"}, names


def test_audit_details_are_redacted_and_categories_cover_brief() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="A", slug="a")
    secret = "sk-supersecretvalue1234567890"
    log = AuditLog(store, redact_values=(secret,))
    row = log.record(
        tenant_id=tenant.id.value,
        principal=_principal(tenant.id.value),
        action="secret.read",
        category=AuditCategory.SECRET_ACCESS,
        outcome=AuditOutcome.DENIED,
        details={"attempted": secret, "note": f"Bearer {secret}"},
    )
    dumped = json.dumps(row.to_dict())
    assert secret not in dumped and "[REDACTED]" in dumped
    expected = {
        "authentication",
        "resource.create",
        "resource.mutate",
        "resource.delete",
        "experiment.launch",
        "policy.decision",
        "approval",
        "denial",
        "secret.access",
        "artifact.access",
        "admin",
        "worker",
    }
    assert {item.value for item in AuditCategory} == expected


def test_redaction_covers_common_credential_shapes() -> None:
    text = (
        "anthropic sk-ant-abcdefghijklmnopqrstuvwxyz1234 | service atsk_ZZZZZZZZZZZZZZZZZZ | "
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijklmnopqrstuvwxyz | postgresql://user:pa55word@db:5432/x"
    )
    out = redact(text)
    for fragment in ("sk-ant-abc", "atsk_ZZZ", "eyJhbGci", "pa55word"):
        assert fragment not in out, out
    assert "postgresql://user:[REDACTED]@db:5432/x" in out


def test_sensitive_env_names_and_logging_filter(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test-key-value-000000")
    monkeypatch.setenv("MATRIX_ENTERPRISE_DATABASE_URL", "postgres://u:secretpw@h/db")
    names = sensitive_env_names()
    assert "OPENAI_API_KEY" in names and "MATRIX_ENTERPRISE_DATABASE_URL" in names

    provider = StaticSecretProvider({"OPENAI_API_KEY": "sk-openai-test-key-value-000000"})
    logger = logging.getLogger("matraix.enterprise.test.redaction")
    logger.propagate = False
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    handler.addFilter(RedactingFilter(provider))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("using key %s for provider", "sk-openai-test-key-value-000000")
    logger.removeHandler(handler)
    assert records and "sk-openai" not in records[0].getMessage()
