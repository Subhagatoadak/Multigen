"""
Prompt control plane — centralised, versioned prompt registry for Multigen.

Problems solved
---------------
- Prompts scattered in agent code (change = code deploy)
- No centralised prompt registry (all prompts in one place, versioned)
- No prompt compilation + templating (variable injection, conditional blocks)
- No prompt A/B testing at registry level
- No prompt performance attribution (which version produced best outputs)
- No prompt inheritance (base prompts extended by agents)
- No prompt access control (every dev can edit production prompts)
- No human prompt review workflow

Classes
-------
- ``PromptVersion``         — a versioned snapshot of a prompt template
- ``PromptTemplate``        — compiled template with variable injection
- ``PromptRegistry``        — central store of named, versioned prompt templates
- ``PromptInheritance``     — extends a base prompt with overrides
- ``PromptABTest``          — A/B test two prompt variants with score tracking
- ``PromptAccessControl``   — RBAC for prompt editing (read / write / admin)
- ``PromptReviewWorkflow``  — staged promotion (draft → review → approved → live)
- ``PromptManager``         — high-level facade

Usage::

    from multigen.prompt_registry import PromptManager

    mgr = PromptManager()
    mgr.create("qa-base", template="Answer the question: {question}")
    mgr.publish("qa-base")

    compiled = mgr.compile("qa-base", question="What is 2+2?")
    print(compiled)   # → "Answer the question: What is 2+2?"
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── PromptVersion ─────────────────────────────────────────────────────────────

@dataclass
class PromptVersion:
    prompt_name: str
    version: str
    template: str
    status: str = "draft"           # draft | under_review | approved | live | archived
    author: str = "unknown"
    parent_version: Optional[str] = None
    variables: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    score: Optional[float] = None   # aggregate performance score

    def extract_variables(self) -> List[str]:
        return re.findall(r"\{(\w+)\}", self.template)


# ── PromptTemplate (compiler) ─────────────────────────────────────────────────

class PromptTemplate:
    """
    Compiled prompt template with variable injection, conditional blocks,
    and loop support.

    Syntax::

        {variable}                  — simple variable substitution
        {%if condition%}...{%endif%} — conditional block (condition = variable name, truthy check)
        {%for item in list_var%}...{%endfor%} — loop over a list variable

    Usage::

        tpl = PromptTemplate("Hello {name}!{%if title%} You are {title}.{%endif%}")
        print(tpl.render(name="Alice", title="Dr"))
        # → "Hello Alice! You are Dr."
    """

    def __init__(self, template: str) -> None:
        self.template = template
        self._variables = re.findall(r"\{(\w+)\}", template)

    def render(self, **variables: Any) -> str:
        text = self.template

        # Process loops: {%for item in list_var%}...{%endfor%}
        def replace_loop(m: re.Match) -> str:
            item_var, list_var = m.group(1), m.group(2)
            body = m.group(3)
            items = variables.get(list_var, [])
            return "".join(body.replace(f"{{{item_var}}}", str(item)) for item in items)

        text = re.sub(
            r"\{%for\s+(\w+)\s+in\s+(\w+)%\}(.*?)\{%endfor%\}",
            replace_loop,
            text,
            flags=re.DOTALL,
        )

        # Process conditionals: {%if var%}...{%endif%}
        def replace_if(m: re.Match) -> str:
            cond_var = m.group(1)
            body = m.group(2)
            return body if variables.get(cond_var) else ""

        text = re.sub(
            r"\{%if\s+(\w+)%\}(.*?)\{%endif%\}",
            replace_if,
            text,
            flags=re.DOTALL,
        )

        # Simple variable substitution
        for key, value in variables.items():
            text = text.replace(f"{{{key}}}", str(value))

        return text.strip()

    @property
    def variables(self) -> List[str]:
        return self._variables


# ── PromptRegistry ────────────────────────────────────────────────────────────

class PromptRegistry:
    """
    Central store of named, versioned prompt templates.

    Usage::

        reg = PromptRegistry()
        reg.create("qa", "Answer: {question}", author="alice")
        reg.publish("qa")   # sets latest version as live
        tpl = reg.get_live("qa")
        print(tpl.render(question="What is 2+2?"))
    """

    def __init__(self) -> None:
        # name → {version_str: PromptVersion}
        self._prompts: Dict[str, Dict[str, PromptVersion]] = {}
        self._live: Dict[str, str] = {}          # name → live version

    def create(
        self,
        name: str,
        template: str,
        author: str = "unknown",
        tags: Optional[List[str]] = None,
        parent_version: Optional[str] = None,
    ) -> PromptVersion:
        existing = self._prompts.get(name, {})
        version = f"{len(existing) + 1}.0"
        pv = PromptVersion(
            prompt_name=name,
            version=version,
            template=template,
            author=author,
            parent_version=parent_version,
            tags=tags or [],
        )
        pv.variables = pv.extract_variables()
        self._prompts.setdefault(name, {})[version] = pv
        return pv

    def get(self, name: str, version: Optional[str] = None) -> PromptVersion:
        versions = self._prompts.get(name, {})
        if not versions:
            raise KeyError(f"Prompt {name!r} not found")
        if version:
            return versions[version]
        return list(versions.values())[-1]

    def get_live(self, name: str) -> PromptTemplate:
        live_ver = self._live.get(name)
        if live_ver is None:
            raise KeyError(f"No live version for prompt {name!r}")
        pv = self._prompts[name][live_ver]
        return PromptTemplate(pv.template)

    def publish(self, name: str, version: Optional[str] = None) -> PromptVersion:
        """Mark a version as live."""
        pv = self.get(name, version)
        pv.status = "live"
        self._live[name] = pv.version
        return pv

    def archive(self, name: str, version: str) -> None:
        pv = self.get(name, version)
        pv.status = "archived"

    def versions(self, name: str) -> List[PromptVersion]:
        return list(self._prompts.get(name, {}).values())

    def compile(self, name: str, **variables: Any) -> str:
        return self.get_live(name).render(**variables)

    def list_prompts(self) -> List[str]:
        return list(self._prompts.keys())


# ── PromptInheritance ─────────────────────────────────────────────────────────

class PromptInheritance:
    """
    Extends a base prompt with section overrides.

    Supports ``{%block name%}default{%endblock%}`` in base templates.
    Subclasses can override individual blocks without duplicating the rest.

    Usage::

        base = "System: {%block persona%}You are a helpful assistant.{%endblock%}\\nQ: {question}"
        agent_tpl = PromptInheritance(base).override("persona", "You are a finance expert.").build()
        print(PromptTemplate(agent_tpl).render(question="What is ROI?"))
    """

    def __init__(self, base_template: str) -> None:
        self._base = base_template
        self._overrides: Dict[str, str] = {}

    def override(self, block_name: str, content: str) -> "PromptInheritance":
        self._overrides[block_name] = content
        return self

    def build(self) -> str:
        text = self._base
        def replace_block(m: re.Match) -> str:
            name = m.group(1)
            default = m.group(2)
            return self._overrides.get(name, default)
        return re.sub(
            r"\{%block\s+(\w+)%\}(.*?)\{%endblock%\}",
            replace_block,
            text,
            flags=re.DOTALL,
        )


# ── Prompt A/B Test ───────────────────────────────────────────────────────────

@dataclass
class PromptABResult:
    winner: Optional[str]
    variant_a: str
    variant_b: str
    score_a: float
    score_b: float
    n_a: int
    n_b: int


class PromptABTest:
    """
    A/B test two prompt variants with score tracking.

    Usage::

        ab = PromptABTest("v1.0", "v2.0")
        variant = ab.select()
        # ... run agent with variant ...
        ab.record(variant, score=0.85)
        result = ab.result()
    """

    def __init__(self, variant_a: str, variant_b: str, split: float = 0.5) -> None:
        self.variant_a = variant_a
        self.variant_b = variant_b
        self.split = split
        self._scores_a: List[float] = []
        self._scores_b: List[float] = []
        self._n = 0

    def select(self) -> str:
        import random
        self._n += 1
        return self.variant_a if random.random() < self.split else self.variant_b

    def record(self, variant: str, score: float) -> None:
        if variant == self.variant_a:
            self._scores_a.append(score)
        else:
            self._scores_b.append(score)

    def result(self) -> PromptABResult:
        sa = sum(self._scores_a) / len(self._scores_a) if self._scores_a else 0.0
        sb = sum(self._scores_b) / len(self._scores_b) if self._scores_b else 0.0
        winner = None
        if self._scores_a and self._scores_b:
            winner = self.variant_a if sa >= sb else self.variant_b
        return PromptABResult(
            winner=winner,
            variant_a=self.variant_a,
            variant_b=self.variant_b,
            score_a=sa,
            score_b=sb,
            n_a=len(self._scores_a),
            n_b=len(self._scores_b),
        )


# ── Prompt Access Control ─────────────────────────────────────────────────────

class PermissionDeniedError(Exception):
    pass


class PromptAccessControl:
    """
    Role-based access control for prompt editing.

    Roles: ``reader`` (can read), ``writer`` (can create/edit drafts),
    ``admin`` (can publish/archive).

    Usage::

        acl = PromptAccessControl()
        acl.grant("alice", "admin")
        acl.grant("bob", "writer")
        acl.check("alice", "publish")   # OK
        acl.check("bob",   "publish")   # raises PermissionDeniedError
    """

    _PERMISSIONS = {
        "reader": {"read"},
        "writer": {"read", "create", "edit"},
        "admin":  {"read", "create", "edit", "publish", "archive", "delete"},
    }

    def __init__(self) -> None:
        self._roles: Dict[str, str] = {}

    def grant(self, user: str, role: str) -> None:
        if role not in self._PERMISSIONS:
            raise ValueError(f"Unknown role {role!r}")
        self._roles[user] = role

    def revoke(self, user: str) -> None:
        self._roles.pop(user, None)

    def check(self, user: str, action: str) -> bool:
        role = self._roles.get(user, "reader")
        perms = self._PERMISSIONS.get(role, set())
        if action not in perms:
            raise PermissionDeniedError(
                f"User {user!r} (role={role!r}) lacks permission for {action!r}"
            )
        return True


# ── Prompt Review Workflow ────────────────────────────────────────────────────

@dataclass
class ReviewRequest:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt_name: str = ""
    version: str = ""
    submitted_by: str = ""
    reviewer: Optional[str] = None
    status: str = "pending"         # pending | approved | rejected
    comments: str = ""
    submitted_at: float = field(default_factory=time.time)
    reviewed_at: Optional[float] = None


class PromptReviewWorkflow:
    """
    Staged promotion: draft → submitted → approved/rejected → live.

    Usage::

        workflow = PromptReviewWorkflow(registry)
        request = workflow.submit("qa-agent", "2.0", submitted_by="alice")
        workflow.approve(request.id, reviewer="bob")   # sets prompt live
    """

    def __init__(self, registry: PromptRegistry) -> None:
        self._registry = registry
        self._requests: Dict[str, ReviewRequest] = {}

    def submit(self, prompt_name: str, version: str, submitted_by: str) -> ReviewRequest:
        pv = self._registry.get(prompt_name, version)
        pv.status = "under_review"
        req = ReviewRequest(
            prompt_name=prompt_name,
            version=version,
            submitted_by=submitted_by,
        )
        self._requests[req.id] = req
        return req

    def approve(self, request_id: str, reviewer: str, comments: str = "") -> ReviewRequest:
        req = self._requests[request_id]
        req.status = "approved"
        req.reviewer = reviewer
        req.comments = comments
        req.reviewed_at = time.time()
        self._registry.publish(req.prompt_name, req.version)
        return req

    def reject(self, request_id: str, reviewer: str, comments: str = "") -> ReviewRequest:
        req = self._requests[request_id]
        req.status = "rejected"
        req.reviewer = reviewer
        req.comments = comments
        req.reviewed_at = time.time()
        pv = self._registry.get(req.prompt_name, req.version)
        pv.status = "draft"
        return req

    def pending(self) -> List[ReviewRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]


# ── PromptManager facade ──────────────────────────────────────────────────────

class PromptManager:
    """
    High-level facade combining registry, ACL, A/B tests, and review workflow.

    Usage::

        mgr = PromptManager()
        mgr.acl.grant("alice", "admin")
        v1 = mgr.create("qa", "Answer: {question}", author="alice")
        mgr.publish("qa")
        print(mgr.compile("qa", question="What is ROI?"))
    """

    def __init__(self) -> None:
        self.registry = PromptRegistry()
        self.acl = PromptAccessControl()
        self.review = PromptReviewWorkflow(self.registry)
        self._ab_tests: Dict[str, PromptABTest] = {}

    def create(self, name: str, template: str, author: str = "unknown", **kwargs: Any) -> PromptVersion:
        return self.registry.create(name, template, author=author, **kwargs)

    def publish(self, name: str, version: Optional[str] = None) -> PromptVersion:
        return self.registry.publish(name, version)

    def compile(self, name: str, **variables: Any) -> str:
        return self.registry.compile(name, **variables)

    def ab_test(self, name: str, version_a: str, version_b: str) -> PromptABTest:
        key = f"{name}:{version_a}:{version_b}"
        self._ab_tests[key] = PromptABTest(version_a, version_b)
        return self._ab_tests[key]


__all__ = [
    "PromptVersion",
    "PromptTemplate",
    "PromptRegistry",
    "PromptInheritance",
    "PromptABResult",
    "PromptABTest",
    "PermissionDeniedError",
    "PromptAccessControl",
    "ReviewRequest",
    "PromptReviewWorkflow",
    "PromptManager",
]
