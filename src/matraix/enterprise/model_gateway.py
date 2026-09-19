"""Model gateway beside LiteLLM.

Types and routing live here so persona prompt code never imports a provider
SDK. Existing LiteLLM usage in ``persona_grounding`` / ``persona_agent_context``
is unchanged. This module does not import ``litellm`` or any vendor client.

Completions default to an in-process sandbox mock. ``ALLOW`` selects a catalog
provider without calling it unless a completer is injected.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from matraix.enterprise.errors import ApprovalRequiredError, PolicyDeniedError
from matraix.enterprise.ids import PersonaId, TenantId
from matraix.enterprise.policy import (
    DataClassification,
    PolicyDecision,
    PolicyEvaluation,
    PolicyRequest,
    env_allowed_providers,
    env_denied_providers,
    evaluate_policy,
)

MODEL_RESIDENCY_ENV = "MATRIX_ENTERPRISE_MODEL_RESIDENCY"
_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9]{8,}|token=)\S*"
)


class ModelCompleter(Protocol):
    def complete(
        self, request: "ModelRequest", provider: "ModelProvider"
    ) -> "ModelResponse": ...


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    chat: bool = True
    tools: bool = False
    json_mode: bool = False
    vision: bool = False
    embeddings: bool = False
    max_context_tokens: int = 8192

    def supports(self, capability: str) -> bool:
        name = (capability or "chat").strip().lower()
        if name in {"chat", "completion", "complete"}:
            return self.chat
        if name in {"tools", "tool", "function_calling"}:
            return self.tools
        if name in {"json", "json_mode"}:
            return self.json_mode
        if name in {"vision", "image"}:
            return self.vision
        if name in {"embeddings", "embed"}:
            return self.embeddings
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat": self.chat,
            "tools": self.tools,
            "json_mode": self.json_mode,
            "vision": self.vision,
            "embeddings": self.embeddings,
            "max_context_tokens": self.max_context_tokens,
        }


@dataclass(frozen=True, slots=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True, slots=True)
class ModelProvider:
    """Named catalog entry. ``credential_env`` is a variable *name* only."""

    name: str
    display_name: str
    residency: str
    capabilities: ModelCapabilities
    cost_score: float
    latency_ms: int
    destination: str
    credential_env: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "residency": self.residency,
            "capabilities": self.capabilities.to_dict(),
            "cost_score": self.cost_score,
            "latency_ms": self.latency_ms,
            "destination": self.destination,
            "credential_env": self.credential_env,
        }


_CHAT_TOOLS_JSON = ModelCapabilities(
    chat=True, tools=True, json_mode=True, max_context_tokens=32_000
)
_CHAT_ONLY = ModelCapabilities(chat=True, json_mode=True, max_context_tokens=8_192)

DEFAULT_MODEL_CATALOG: tuple[ModelProvider, ...] = (
    ModelProvider(
        name="sandbox",
        display_name="Sandbox mock",
        residency="local",
        capabilities=ModelCapabilities(
            chat=True,
            tools=True,
            json_mode=True,
            vision=True,
            embeddings=True,
            max_context_tokens=32_000,
        ),
        cost_score=0.0,
        latency_ms=1,
        destination="sandbox",
    ),
    ModelProvider(
        name="anthropic",
        display_name="Anthropic",
        residency="us",
        capabilities=_CHAT_TOOLS_JSON,
        cost_score=0.45,
        latency_ms=800,
        destination="external",
        credential_env="ANTHROPIC_API_KEY",
    ),
    ModelProvider(
        name="openai",
        display_name="OpenAI",
        residency="us",
        capabilities=_CHAT_TOOLS_JSON,
        cost_score=0.40,
        latency_ms=700,
        destination="external",
        credential_env="OPENAI_API_KEY",
    ),
    ModelProvider(
        name="gemini",
        display_name="Gemini",
        residency="us",
        capabilities=_CHAT_TOOLS_JSON,
        cost_score=0.35,
        latency_ms=750,
        destination="external",
        credential_env="GEMINI_API_KEY",
    ),
    ModelProvider(
        name="eu-anthropic",
        display_name="Anthropic (EU residency)",
        residency="eu",
        capabilities=_CHAT_TOOLS_JSON,
        cost_score=0.48,
        latency_ms=900,
        destination="external",
        credential_env="ANTHROPIC_API_KEY",
    ),
    ModelProvider(
        name="local-ollama",
        display_name="Local Ollama",
        residency="local",
        capabilities=_CHAT_ONLY,
        cost_score=0.05,
        latency_ms=200,
        destination="local",
    ),
)


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    """Tenant routing and exposure policy for the model gateway."""

    tenant_id: TenantId
    allowed_providers: tuple[str, ...] = ()
    denied_providers: tuple[str, ...] = ()
    required_residency: str | None = None
    max_cost_score: float | None = None
    max_latency_ms: int | None = None
    allowed_capabilities: tuple[str, ...] = ()
    allow_external: bool = False
    allow_live: bool = False
    default_decision: PolicyDecision = PolicyDecision.SANDBOX_ONLY
    denied_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_providers",
            tuple(item.strip().lower() for item in self.allowed_providers if item.strip()),
        )
        object.__setattr__(
            self,
            "denied_providers",
            tuple(item.strip().lower() for item in self.denied_providers if item.strip()),
        )
        object.__setattr__(
            self,
            "allowed_capabilities",
            tuple(
                item.strip().lower() for item in self.allowed_capabilities if item.strip()
            ),
        )
        object.__setattr__(
            self,
            "denied_actions",
            tuple(item.strip().lower() for item in self.denied_actions if item.strip()),
        )
        residency = (self.required_residency or "").strip().lower() or None
        object.__setattr__(self, "required_residency", residency)
        if isinstance(self.default_decision, str):
            object.__setattr__(
                self, "default_decision", PolicyDecision(self.default_decision)
            )
        if self.max_cost_score is not None and not (0.0 <= float(self.max_cost_score) <= 1.0):
            raise ValueError("max_cost_score must be between 0 and 1")
        if self.max_latency_ms is not None and int(self.max_latency_ms) < 0:
            raise ValueError("max_latency_ms must be >= 0")

    def merged_with_env(self) -> "ModelPolicy":
        residency = self.required_residency or (
            os.environ.get(MODEL_RESIDENCY_ENV, "").strip().lower() or None
        )
        allowed = tuple(dict.fromkeys(self.allowed_providers + env_allowed_providers()))
        denied = tuple(dict.fromkeys(self.denied_providers + env_denied_providers()))
        return ModelPolicy(
            tenant_id=self.tenant_id,
            allowed_providers=allowed,
            denied_providers=denied,
            required_residency=residency,
            max_cost_score=self.max_cost_score,
            max_latency_ms=self.max_latency_ms,
            allowed_capabilities=self.allowed_capabilities,
            allow_external=self.allow_external,
            allow_live=self.allow_live,
            default_decision=self.default_decision,
            denied_actions=self.denied_actions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "allowed_providers": list(self.allowed_providers),
            "denied_providers": list(self.denied_providers),
            "required_residency": self.required_residency,
            "max_cost_score": self.max_cost_score,
            "max_latency_ms": self.max_latency_ms,
            "allowed_capabilities": list(self.allowed_capabilities),
            "allow_external": self.allow_external,
            "allow_live": self.allow_live,
            "default_decision": self.default_decision.value,
            "denied_actions": list(self.denied_actions),
        }


def default_model_policy(tenant_id: TenantId) -> ModelPolicy:
    return ModelPolicy(tenant_id=tenant_id)


def resolve_model_policy(
    tenant_id: TenantId, stored: ModelPolicy | None = None
) -> ModelPolicy:
    base = stored if stored is not None else default_model_policy(tenant_id)
    return base.merged_with_env()


@dataclass(frozen=True, slots=True)
class ModelRequest:
    tenant_id: TenantId
    messages: tuple[dict[str, str], ...] = ()
    action: str = "complete"
    resource: str = "model.complete"
    persona_id: PersonaId | None = None
    model_provider: str | None = None
    required_capability: str = "chat"
    residency: str | None = None
    task_complexity: str = "standard"
    data_classification: DataClassification = DataClassification.INTERNAL
    destination: str | None = None
    max_tokens: int = 256
    approved: bool = False
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        complexity = (self.task_complexity or "standard").strip().lower()
        if complexity not in {"simple", "standard", "complex"}:
            raise ValueError("task_complexity must be simple, standard, or complex")
        object.__setattr__(self, "task_complexity", complexity)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if isinstance(self.data_classification, str):
            object.__setattr__(
                self,
                "data_classification",
                DataClassification(self.data_classification),
            )

    def to_policy_request(self, *, destination: str | None = None) -> PolicyRequest:
        return PolicyRequest(
            tenant_id=self.tenant_id,
            action=self.action,
            resource=self.resource,
            persona_id=self.persona_id,
            model_provider=self.model_provider,
            data_classification=self.data_classification,
            destination=destination if destination is not None else self.destination,
            approved=self.approved,
            attributes=self.attributes,
        )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    provider: str
    decision: PolicyDecision
    usage: ModelUsage = field(default_factory=ModelUsage)
    reasons: tuple[str, ...] = ()
    redacted: bool = False
    held_for_approval: bool = False
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "provider": self.provider,
            "decision": self.decision.value,
            "usage": self.usage.to_dict(),
            "reasons": list(self.reasons),
            "redacted": self.redacted,
            "held_for_approval": self.held_for_approval,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True, slots=True)
class ModelRoute:
    provider: ModelProvider | None
    decision: PolicyDecision
    reasons: tuple[str, ...]
    candidates: tuple[str, ...]
    evaluation: PolicyEvaluation

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.to_dict() if self.provider else None,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "candidates": list(self.candidates),
            "evaluation": self.evaluation.to_dict(),
        }


def _provider_by_name(
    name: str | None, catalog: tuple[ModelProvider, ...]
) -> ModelProvider | None:
    if not name:
        return None
    wanted = name.strip().lower()
    for item in catalog:
        if item.name == wanted:
            return item
    return None


def _sandbox_provider(catalog: tuple[ModelProvider, ...]) -> ModelProvider:
    found = _provider_by_name("sandbox", catalog)
    if found is not None:
        return found
    return DEFAULT_MODEL_CATALOG[0]


def infer_destination(
    request: ModelRequest, catalog: tuple[ModelProvider, ...] = DEFAULT_MODEL_CATALOG
) -> str:
    if request.destination:
        return request.destination.strip().lower()
    provider = _provider_by_name(request.model_provider, catalog)
    if provider is not None:
        return provider.destination
    if request.model_provider and request.model_provider.strip().lower() not in {
        "sandbox",
        "local",
        "local-ollama",
    }:
        return "external"
    return "sandbox"


def _filter_candidates(
    request: ModelRequest,
    policy: ModelPolicy,
    catalog: tuple[ModelProvider, ...],
) -> list[ModelProvider]:
    residency = request.residency or policy.required_residency
    allowed = policy.allowed_providers
    denied = set(policy.denied_providers)
    items: list[ModelProvider] = []
    for provider in catalog:
        if provider.name in denied:
            continue
        if allowed and provider.name not in allowed and provider.name != "sandbox":
            continue
        if not provider.capabilities.supports(request.required_capability):
            continue
        if (
            policy.allowed_capabilities
            and request.required_capability not in policy.allowed_capabilities
            and request.required_capability not in {"chat", "complete", "completion"}
        ):
            continue
        if residency and provider.residency != residency and provider.name != "sandbox":
            continue
        if policy.max_cost_score is not None and provider.cost_score > policy.max_cost_score:
            continue
        if policy.max_latency_ms is not None and provider.latency_ms > policy.max_latency_ms:
            continue
        items.append(provider)
    return items


def _score_provider(provider: ModelProvider, request: ModelRequest) -> float:
    complexity = request.task_complexity
    if complexity == "simple":
        score = provider.cost_score * 2.0 + provider.latency_ms / 1000.0
    elif complexity == "complex":
        score = provider.cost_score * 0.3 + provider.latency_ms / 5000.0
        if not provider.capabilities.tools:
            score += 2.0
        if provider.capabilities.max_context_tokens < 16_000:
            score += 1.0
    else:
        score = provider.cost_score + provider.latency_ms / 2000.0
    if request.residency and provider.residency == request.residency:
        score -= 0.5
    if request.model_provider and provider.name == request.model_provider.strip().lower():
        score -= 1.0
    return score


def route_model(
    request: ModelRequest,
    policy: ModelPolicy | None = None,
    *,
    catalog: tuple[ModelProvider, ...] = DEFAULT_MODEL_CATALOG,
) -> ModelRoute:
    """Select a catalog provider. Persona code must not call this with an SDK."""
    resolved = resolve_model_policy(request.tenant_id, policy)
    destination = infer_destination(request, catalog)
    evaluation = evaluate_policy(
        request.to_policy_request(destination=destination), resolved
    )
    sandbox = _sandbox_provider(catalog)
    if evaluation.decision is PolicyDecision.DENY:
        return ModelRoute(
            provider=None,
            decision=evaluation.decision,
            reasons=evaluation.reasons,
            candidates=(),
            evaluation=evaluation,
        )
    if evaluation.decision in {
        PolicyDecision.SANDBOX_ONLY,
        PolicyDecision.ALLOW_WITH_REDACTION,
        PolicyDecision.ALLOW_WITH_APPROVAL,
    }:
        return ModelRoute(
            provider=sandbox,
            decision=evaluation.decision,
            reasons=evaluation.reasons + ("routed to sandbox provider",),
            candidates=(sandbox.name,),
            evaluation=evaluation,
        )
    candidates = _filter_candidates(request, resolved, catalog)
    external = [item for item in candidates if item.destination == "external"]
    pool = external or candidates
    if not pool:
        return ModelRoute(
            provider=sandbox,
            decision=PolicyDecision.SANDBOX_ONLY,
            reasons=evaluation.reasons + ("no catalog candidate; sandbox fallback",),
            candidates=(sandbox.name,),
            evaluation=evaluation,
        )
    ranked = sorted(pool, key=lambda item: _score_provider(item, request))
    chosen = ranked[0]
    return ModelRoute(
        provider=chosen,
        decision=evaluation.decision,
        reasons=evaluation.reasons + (f"routed to {chosen.name}",),
        candidates=tuple(item.name for item in ranked),
        evaluation=evaluation,
    )


def _last_user_text(messages: tuple[dict[str, str], ...]) -> str:
    for item in reversed(messages):
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role == "user" and content.strip():
            return content
    if messages:
        return str(messages[-1].get("content") or "")
    return ""


def _redact(text: str) -> tuple[str, bool]:
    redacted, count = _SECRETISH.subn("[REDACTED]", text)
    return redacted, count > 0


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


class SandboxCompleter:
    """Deterministic in-process mock. No network, no provider SDK."""

    def complete(self, request: ModelRequest, provider: ModelProvider) -> ModelResponse:
        prompt = _last_user_text(request.messages)
        body, redacted = _redact(prompt)
        content = f"[sandbox:{provider.name}] {body or 'no prompt'}"
        prompt_tokens = _estimate_tokens(prompt)
        completion_tokens = _estimate_tokens(content)
        return ModelResponse(
            content=content[: request.max_tokens * 8],
            provider=provider.name,
            decision=PolicyDecision.SANDBOX_ONLY,
            usage=ModelUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=0.0,
            ),
            reasons=("sandbox completer; no provider SDK called",),
            redacted=redacted,
        )


def complete_model(
    request: ModelRequest,
    policy: ModelPolicy | None = None,
    *,
    catalog: tuple[ModelProvider, ...] = DEFAULT_MODEL_CATALOG,
    completer: ModelCompleter | None = None,
) -> ModelResponse:
    """Execute a completion through the policy gateway.

    DENY raises. ALLOW_WITH_APPROVAL without ``approved`` raises.
    SANDBOX_ONLY / redaction use the sandbox mock. ALLOW is a dry-run
    provider selection unless ``completer`` is injected (tests / adapters).
    """
    routed = route_model(request, policy, catalog=catalog)
    evaluation = routed.evaluation
    if evaluation.decision is PolicyDecision.DENY:
        raise PolicyDeniedError(
            f"policy denied: {'; '.join(evaluation.reasons)}",
            reasons=evaluation.reasons,
            decision=evaluation.decision.value,
        )
    if evaluation.decision is PolicyDecision.ALLOW_WITH_APPROVAL and not request.approved:
        raise ApprovalRequiredError(
            f"policy requires approval: {'; '.join(evaluation.reasons)}",
            reasons=evaluation.reasons,
            decision=evaluation.decision.value,
        )
    provider = routed.provider or _sandbox_provider(catalog)
    if evaluation.decision is PolicyDecision.ALLOW and completer is not None:
        response = completer.complete(request, provider)
        return ModelResponse(
            content=response.content,
            provider=provider.name,
            decision=PolicyDecision.ALLOW,
            usage=response.usage,
            reasons=routed.reasons + response.reasons,
            redacted=response.redacted,
            dry_run=response.dry_run,
        )
    if evaluation.decision is PolicyDecision.ALLOW:
        return ModelResponse(
            content="",
            provider=provider.name,
            decision=PolicyDecision.ALLOW,
            usage=ModelUsage(),
            reasons=routed.reasons
            + ("live completion disabled; provider selected only",),
            dry_run=True,
        )
    mock = (completer or SandboxCompleter()).complete(request, _sandbox_provider(catalog))
    redacted = mock.redacted or evaluation.redaction_required
    content = mock.content
    if evaluation.redaction_required:
        content, extra = _redact(content)
        redacted = redacted or extra
    return ModelResponse(
        content=content,
        provider="sandbox",
        decision=evaluation.decision,
        usage=mock.usage,
        reasons=routed.reasons + mock.reasons,
        redacted=redacted,
        held_for_approval=evaluation.approval_required and request.approved,
    )


@dataclass
class ModelGateway:
    """Thin object wrapper around :func:`route_model` / :func:`complete_model`."""

    catalog: tuple[ModelProvider, ...] = DEFAULT_MODEL_CATALOG
    completer: ModelCompleter | None = None

    def route(
        self, request: ModelRequest, policy: ModelPolicy | None = None
    ) -> ModelRoute:
        return route_model(request, policy, catalog=self.catalog)

    def complete(
        self, request: ModelRequest, policy: ModelPolicy | None = None
    ) -> ModelResponse:
        return complete_model(
            request, policy, catalog=self.catalog, completer=self.completer
        )


def catalog_for_policy(
    policy: ModelPolicy,
    *,
    catalog: tuple[ModelProvider, ...] = DEFAULT_MODEL_CATALOG,
) -> list[dict[str, Any]]:
    resolved = policy.merged_with_env()
    denied = set(resolved.denied_providers)
    allowed = resolved.allowed_providers
    rows = []
    for provider in catalog:
        permitted = provider.name not in denied and (
            not allowed or provider.name in allowed or provider.name == "sandbox"
        )
        row = provider.to_dict()
        row["allowed"] = permitted
        rows.append(row)
    return rows
