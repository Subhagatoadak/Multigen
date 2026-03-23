"""Helpers to apply manifest policies to runtime components."""
from __future__ import annotations

from ..message_bus import CommunicationHub
from ...manifests.validators import WorkflowManifest
from .rate_limit import MultiLimiter, RateLimiter
from ..prompting import PromptManager


def apply_namespace_policies(manifest: WorkflowManifest, hub: CommunicationHub) -> None:
    """Apply namespace ACL policies defined in the manifest to the communication hub."""

    policies = manifest.policies or {}
    for policy in policies.values():
        if policy.type != "namespace_acl":
            continue
        allow = policy.rules.get("allow", [])
        deny = policy.rules.get("deny", [])
        for ns in policy.namespaces or []:
            hub.set_namespace_acl(ns, allow=allow, deny=deny)


def apply_policies(manifest: WorkflowManifest, *, hub: CommunicationHub | None = None) -> None:
    """Apply known policy types to runtime components."""

    if hub:
        apply_namespace_policies(manifest, hub)


def apply_rate_limits(
    manifest: WorkflowManifest, *, limiter: MultiLimiter, hub: CommunicationHub | None = None
) -> None:
    """Configure rate limits for namespaces/agents/tools based on manifest policies."""

    rate_policies = [p for p in manifest.policies.values() if p.type == "rate_limit"] if manifest.policies else []
    rate_limits = manifest.rate_limits or {}
    for policy in rate_policies:
        targets = policy.rules or {}
        if "namespace" in targets:
            for ns, cfg in targets["namespace"].items():
                limiter.set_limit(
                    "namespace", ns, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1))
                )
                if hub:
                    hub.set_namespace_rate_limit(ns, cfg.get("rate", 1), cfg.get("capacity", 1))
        if "agent" in targets:
            for ag, cfg in targets["agent"].items():
                limiter.set_limit(
                    "agent", ag, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1))
                )
        if "tool" in targets:
            for tool, cfg in targets["tool"].items():
                limiter.set_limit(
                    "tool", tool, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1))
                )
    # Apply inline rate_limits block if present
    for ns, cfg in rate_limits.get("namespaces", {}).items():
        limiter.set_limit(
            "namespace", ns, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1))
        )
        if hub:
            hub.set_namespace_rate_limit(ns, cfg.get("rate", 1), cfg.get("capacity", 1))
    for ag, cfg in rate_limits.get("agents", {}).items():
        limiter.set_limit(
            "agent", ag, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1))
        )
    for tool, cfg in rate_limits.get("tools", {}).items():
        limiter.set_limit("tool", tool, RateLimiter(rate_per_sec=cfg.get("rate", 1), capacity=cfg.get("capacity", 1)))


def load_prompts(manifest: WorkflowManifest, manager: PromptManager) -> None:
    """Register prompts from manifest prompts section."""

    for name, cfg in (manifest.prompts or {}).items():
        template = cfg.get("template", "")
        version = cfg.get("version")
        metadata = cfg.get("metadata", {})
        manager.add(name, template, version=version, metadata=metadata)


__all__ = ["apply_policies", "apply_namespace_policies", "apply_rate_limits", "load_prompts"]


__all__ = ["apply_policies", "apply_namespace_policies"]
