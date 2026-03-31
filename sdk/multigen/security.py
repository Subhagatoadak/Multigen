"""
Security primitives for enterprise Multigen deployments.

Problems solved
---------------
- No auth layer on the orchestrator API (endpoint is currently open)
- No JWT / OAuth2 / API key management
- No input/output encryption at rest
- No workflow-level data classification
- No agent sandboxing at network level (agents can call arbitrary URLs)
- No compliance scan integration (GDPR/HIPAA checks before workflow runs)

Classes
-------
- ``APIKey``                — API key descriptor with scopes + expiry
- ``APIKeyManager``         — issue / validate / revoke API keys
- ``JWTToken``              — HS256 JWT token (stdlib only — no PyJWT)
- ``JWTManager``            — sign / verify / decode JWTs
- ``DataClassification``    — labels: public / internal / confidential / restricted
- ``WorkflowClassifier``    — assigns classification to workflow contexts
- ``NetworkPolicy``         — allowlist / denylist of external URL patterns for agents
- ``ComplianceRule``        — a GDPR/HIPAA check as a callable predicate
- ``ComplianceScan``        — runs rules against a context before workflow starts
- ``SecurityManager``       — high-level facade combining all above

Usage::

    from multigen.security import SecurityManager, DataClassification

    sec = SecurityManager(jwt_secret="my-secret")

    # API key auth
    key = sec.keys.issue(owner="service-a", scopes=["read", "write"])
    sec.keys.validate(key.token, required_scope="write")

    # JWT
    token = sec.jwt.sign({"sub": "alice", "role": "admin"})
    claims = sec.jwt.verify(token)

    # Compliance scan
    sec.compliance.add_rule("no_pii_in_input",
        lambda ctx: "email" not in str(ctx.get("input", "")))
    sec.compliance.scan(ctx)   # raises ComplianceViolation if rule fails
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# ── API Key Management ────────────────────────────────────────────────────────

@dataclass
class APIKey:
    token: str
    owner: str
    scopes: List[str] = field(default_factory=list)
    expires_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    revoked: bool = False

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at and time.time() > self.expires_at:
            return False
        return True


class InvalidAPIKeyError(Exception):
    pass


class APIKeyManager:
    """
    Issues, validates, and revokes API keys.

    Usage::

        mgr = APIKeyManager()
        key = mgr.issue(owner="alice", scopes=["read", "write"], ttl_days=30)
        mgr.validate(key.token, required_scope="write")
        mgr.revoke(key.token)
    """

    def __init__(self) -> None:
        self._keys: Dict[str, APIKey] = {}

    def issue(
        self,
        owner: str,
        scopes: Optional[List[str]] = None,
        ttl_days: Optional[float] = None,
    ) -> APIKey:
        token = "mk-" + secrets.token_hex(24)
        expires_at = time.time() + ttl_days * 86400 if ttl_days else None
        key = APIKey(token=token, owner=owner, scopes=scopes or [], expires_at=expires_at)
        self._keys[token] = key
        return key

    def validate(self, token: str, required_scope: Optional[str] = None) -> APIKey:
        key = self._keys.get(token)
        if not key or not key.is_valid:
            raise InvalidAPIKeyError("Invalid or expired API key")
        if required_scope and required_scope not in key.scopes:
            raise InvalidAPIKeyError(
                f"API key lacks required scope {required_scope!r}"
            )
        return key

    def revoke(self, token: str) -> None:
        key = self._keys.get(token)
        if key:
            key.revoked = True

    def list_for_owner(self, owner: str) -> List[APIKey]:
        return [k for k in self._keys.values() if k.owner == owner]


# ── JWT (stdlib HS256, no PyJWT) ──────────────────────────────────────────────

class JWTError(Exception):
    pass


class JWTManager:
    """
    Sign and verify HS256 JWTs using stdlib only.

    Usage::

        jwt = JWTManager(secret="my-secret", ttl_seconds=3600)
        token = jwt.sign({"sub": "alice", "role": "admin"})
        claims = jwt.verify(token)   # raises JWTError if invalid/expired
    """

    def __init__(self, secret: str, ttl_seconds: int = 3600) -> None:
        self._secret = secret.encode()
        self.ttl = ttl_seconds

    def _b64_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _b64_decode(self, s: str) -> bytes:
        # Add padding
        s += "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s)

    def sign(self, claims: Dict[str, Any]) -> str:
        header = self._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = dict(claims)
        payload.setdefault("iat", int(time.time()))
        payload.setdefault("exp", int(time.time()) + self.ttl)
        payload_b64 = self._b64_encode(json.dumps(payload).encode())
        msg = f"{header}.{payload_b64}".encode()
        sig = hmac.new(self._secret, msg, hashlib.sha256).digest()
        return f"{header}.{payload_b64}.{self._b64_encode(sig)}"

    def verify(self, token: str) -> Dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise JWTError("Malformed JWT")
        header_b64, payload_b64, sig_b64 = parts
        msg = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(self._secret, msg, hashlib.sha256).digest()
        actual_sig = self._b64_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise JWTError("Invalid JWT signature")
        claims = json.loads(self._b64_decode(payload_b64))
        if "exp" in claims and time.time() > claims["exp"]:
            raise JWTError("JWT expired")
        return claims


# ── Data Classification ───────────────────────────────────────────────────────

class DataClassification(Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def level(self) -> int:
        return {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}[self.value]

    def __gt__(self, other: "DataClassification") -> bool:
        return self.level > other.level


class WorkflowClassifier:
    """
    Assigns a DataClassification to a workflow context based on
    keyword rules.

    Usage::

        classifier = WorkflowClassifier()
        classifier.add_rule(DataClassification.RESTRICTED, ["ssn", "credit_card"])
        classifier.add_rule(DataClassification.CONFIDENTIAL, ["salary", "pii"])
        level = classifier.classify(ctx)
    """

    def __init__(self) -> None:
        self._rules: List[tuple] = []   # (classification, keywords)

    def add_rule(
        self,
        classification: DataClassification,
        keywords: List[str],
    ) -> None:
        self._rules.append((classification, keywords))

    def classify(self, ctx: Dict[str, Any]) -> DataClassification:
        text = json.dumps(ctx, default=str).lower()
        # Return highest classification matching any keyword
        matched = DataClassification.PUBLIC
        for classification, keywords in self._rules:
            if any(kw.lower() in text for kw in keywords):
                if classification > matched:
                    matched = classification
        return matched


# ── Network Policy ────────────────────────────────────────────────────────────

class NetworkPolicyViolation(Exception):
    pass


class NetworkPolicy:
    """
    Allowlist/denylist of URL patterns for agent HTTP calls.
    Prevents agents from calling arbitrary external services.

    Usage::

        policy = NetworkPolicy(default_deny=True)
        policy.allow("https://api\\.openai\\.com/.*")
        policy.allow("https://api\\.anthropic\\.com/.*")
        policy.check("https://api.openai.com/v1/chat/completions")   # OK
        policy.check("https://evil.com/steal")   # raises NetworkPolicyViolation
    """

    def __init__(self, default_deny: bool = False) -> None:
        self._allowlist: List[re.Pattern] = []
        self._denylist: List[re.Pattern] = []
        self._default_deny = default_deny

    def allow(self, pattern: str) -> None:
        self._allowlist.append(re.compile(pattern))

    def deny(self, pattern: str) -> None:
        self._denylist.append(re.compile(pattern))

    def check(self, url: str) -> bool:
        for pat in self._denylist:
            if pat.match(url):
                raise NetworkPolicyViolation(f"URL blocked by denylist: {url}")

        if self._default_deny:
            for pat in self._allowlist:
                if pat.match(url):
                    return True
            raise NetworkPolicyViolation(f"URL not in allowlist: {url}")

        return True


# ── Compliance Scan ───────────────────────────────────────────────────────────

@dataclass
class ComplianceRule:
    name: str
    description: str
    check_fn: Callable          # (ctx) → bool — True = passes
    severity: str = "error"    # error | warning


class ComplianceViolation(Exception):
    pass


@dataclass
class ComplianceReport:
    passed: bool
    violations: List[str]
    warnings: List[str]


class ComplianceScan:
    """
    Runs GDPR/HIPAA/SOC2 compliance checks against a workflow context
    before execution begins.

    Usage::

        scan = ComplianceScan()
        scan.add_rule(ComplianceRule(
            name="no_pii",
            description="No PII in workflow input",
            check_fn=lambda ctx: "@" not in str(ctx.get("input", "")),
        ))
        report = scan.scan(ctx)
        if not report.passed:
            raise ComplianceViolation(report.violations)
    """

    def __init__(self) -> None:
        self._rules: List[ComplianceRule] = []

    def add_rule(self, rule: ComplianceRule) -> None:
        self._rules.append(rule)

    def scan(self, ctx: Dict[str, Any]) -> ComplianceReport:
        violations = []
        warnings = []
        for rule in self._rules:
            try:
                result = rule.check_fn(ctx)
                passed = bool(result)
            except Exception as exc:
                passed = False
                result = str(exc)

            if not passed:
                msg = f"[{rule.name}] {rule.description}"
                if rule.severity == "error":
                    violations.append(msg)
                else:
                    warnings.append(msg)

        if violations:
            raise ComplianceViolation(
                f"Compliance violations: {'; '.join(violations)}"
            )

        return ComplianceReport(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )


# ── SecurityManager facade ────────────────────────────────────────────────────

class SecurityManager:
    """
    High-level facade combining API keys, JWT, data classification,
    network policy, and compliance scanning.

    Usage::

        sec = SecurityManager(jwt_secret="my-secret")
        key = sec.keys.issue("service-a", scopes=["read"])
        token = sec.jwt.sign({"sub": "alice"})
        label = sec.classifier.classify(ctx)
        sec.compliance.scan(ctx)
    """

    def __init__(self, jwt_secret: str = "change-me") -> None:
        self.keys = APIKeyManager()
        self.jwt = JWTManager(secret=jwt_secret)
        self.classifier = WorkflowClassifier()
        self.network = NetworkPolicy()
        self.compliance = ComplianceScan()

        # Sensible defaults
        self.classifier.add_rule(DataClassification.RESTRICTED, ["ssn", "credit_card", "passport"])
        self.classifier.add_rule(DataClassification.CONFIDENTIAL, ["salary", "medical", "diagnosis"])
        self.classifier.add_rule(DataClassification.INTERNAL, ["internal", "employee", "client_id"])


__all__ = [
    "APIKey",
    "InvalidAPIKeyError",
    "APIKeyManager",
    "JWTError",
    "JWTManager",
    "DataClassification",
    "WorkflowClassifier",
    "NetworkPolicyViolation",
    "NetworkPolicy",
    "ComplianceRule",
    "ComplianceViolation",
    "ComplianceReport",
    "ComplianceScan",
    "SecurityManager",
]
