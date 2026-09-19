"""Org-graph CRUD and tenant isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterprisePersona,
    EnterpriseSchemaError,
    EntityNotFoundError,
    InMemoryEnterpriseStore,
    OrgEdge,
    OrgEdgeId,
    OrgNodeKind,
    OrgRelation,
    PersonaId,
    SqliteEnterpriseStore,
    Team,
    TeamId,
    create_tenant_with_default_org,
    new_id,
)
from matraix.enterprise.ids import EntityKind


def _persona(store, tenant, org, body: str) -> EnterprisePersona:
    persona = EnterprisePersona.from_legacy_record(
        tenant_id=tenant.id,
        organization_id=org.id,
        persona_id=PersonaId(tenant.id, body),
        record={
            "persona_id": body[-4:] if len(body) >= 4 else "0001",
            "version": "1.0",
            "source": "synthetic",
            "dimensions": {"age_bracket": "25-34", "role_function": "Teaching"},
        },
    )
    return store.put_persona(persona)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        backend = InMemoryEnterpriseStore()
    else:
        backend = SqliteEnterpriseStore(tmp_path / "graph.sqlite")
    yield backend
    backend.close()


def test_reports_to_round_trip_and_delete(store) -> None:
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    manager = _persona(store, tenant, org, "per_manager")
    report = _persona(store, tenant, org, "per_report")
    edge = store.put_org_edge(
        OrgEdge(
            id=OrgEdgeId(tenant.id, new_id(EntityKind.ORG_EDGE)),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation=OrgRelation.REPORTS_TO,
            source_kind=OrgNodeKind.PERSONA,
            source_id=report.id.value,
            target_kind=OrgNodeKind.PERSONA,
            target_id=manager.id.value,
        )
    )
    loaded = store.get_org_edge(tenant.id, edge.id)
    assert loaded.relation is OrgRelation.REPORTS_TO
    assert loaded.source_id == report.id.value
    listed = store.list_org_edges(tenant.id, relation=OrgRelation.REPORTS_TO)
    assert [item.id for item in listed] == [edge.id]
    store.delete_org_edge(tenant.id, edge.id)
    with pytest.raises(EntityNotFoundError):
        store.get_org_edge(tenant.id, edge.id)


def test_member_of_team_and_owns_system(store) -> None:
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    persona = _persona(store, tenant, org, "per_member")
    team = store.put_team(
        Team(
            id=TeamId(tenant.id, "tea_platform"),
            tenant_id=tenant.id,
            organization_id=org.id,
            department_id=None,
            name="Platform",
        )
    )
    store.put_org_edge(
        OrgEdge(
            id=OrgEdgeId(tenant.id, new_id(EntityKind.ORG_EDGE)),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation="member_of",
            source_kind="persona",
            source_id=persona.id.value,
            target_kind="team",
            target_id=team.id.value,
        )
    )
    store.put_org_edge(
        OrgEdge(
            id=OrgEdgeId(tenant.id, new_id(EntityKind.ORG_EDGE)),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation=OrgRelation.OWNS_SYSTEM,
            source_kind=OrgNodeKind.TEAM,
            source_id=team.id.value,
            target_kind=OrgNodeKind.SYSTEM,
            target_id="sys_billing",
        )
    )
    kinds = {item.relation for item in store.list_org_edges(tenant.id)}
    assert kinds == {OrgRelation.MEMBER_OF, OrgRelation.OWNS_SYSTEM}


def test_graph_rejects_self_loop_and_bad_ends() -> None:
    tenant, org = create_tenant_with_default_org(
        InMemoryEnterpriseStore(), name="Acme", slug="acme"
    )
    with pytest.raises(EnterpriseSchemaError, match="self-loop"):
        OrgEdge(
            id=OrgEdgeId(tenant.id, "edg_loop"),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation=OrgRelation.REPORTS_TO,
            source_kind=OrgNodeKind.PERSONA,
            source_id="per_same",
            target_kind=OrgNodeKind.PERSONA,
            target_id="per_same",
        )
    with pytest.raises(EnterpriseSchemaError, match="target_kind"):
        OrgEdge(
            id=OrgEdgeId(tenant.id, "edg_bad"),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation=OrgRelation.MEMBER_OF,
            source_kind=OrgNodeKind.PERSONA,
            source_id="per_a",
            target_kind=OrgNodeKind.PERSONA,
            target_id="per_b",
        )


def test_graph_isolation_across_tenants(store) -> None:
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _bravo_org = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    manager = _persona(store, alpha, alpha_org, "per_manager")
    report = _persona(store, alpha, alpha_org, "per_report")
    edge = store.put_org_edge(
        OrgEdge(
            id=OrgEdgeId(alpha.id, "edg_alpha"),
            tenant_id=alpha.id,
            organization_id=alpha_org.id,
            relation=OrgRelation.COLLABORATES_WITH,
            source_kind=OrgNodeKind.PERSONA,
            source_id=report.id.value,
            target_kind=OrgNodeKind.PERSONA,
            target_id=manager.id.value,
        )
    )
    with pytest.raises(CrossTenantAccessError):
        store.get_org_edge(bravo.id, edge.id)
    assert store.list_org_edges(bravo.id) == []


def test_duplicate_edge_rejected(store) -> None:
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    a = _persona(store, tenant, org, "per_a")
    b = _persona(store, tenant, org, "per_b")
    kwargs = dict(
        tenant_id=tenant.id,
        organization_id=org.id,
        relation=OrgRelation.APPROVES,
        source_kind=OrgNodeKind.PERSONA,
        source_id=a.id.value,
        target_kind=OrgNodeKind.PERSONA,
        target_id=b.id.value,
    )
    store.put_org_edge(OrgEdge(id=OrgEdgeId(tenant.id, "edg_one"), **kwargs))
    with pytest.raises(EnterpriseSchemaError, match="duplicate"):
        store.put_org_edge(OrgEdge(id=OrgEdgeId(tenant.id, "edg_two"), **kwargs))
