"""
agent_trust.py — Agentic Trust and Provenance for Multigen SDK

Problems this module solves:

1. **Agent Impersonation**: In multi-agent workflows an attacker (or a buggy orchestrator)
   may substitute one agent for another that has different behaviour or intent.
   `AgentAttestor` computes a SHA-256 hash of the agent's source code at registration
   time, binds it to a stable `AgentIdentity`, and lets any caller re-verify that the
   object running today is byte-for-byte the same code that was registered.

2. **Unverified Code Execution**: Dynamically-loaded or hot-patched agent classes can
   silently change behaviour between calls.  The HMAC-SHA256 attestation in
   `AgentAttestor` ties the identity to a shared secret so a compromised environment
   cannot forge a valid identity without knowing the secret.

3. **Output Tampering**: Agent outputs passed through message buses or storage layers can
   be altered before downstream agents consume them.  `WorkflowProvenanceChain` hashes
   every input/output pair, signs the record with HMAC, and Merkle-links each record to
   its parent so any post-hoc modification is detectable via `verify_chain()`.
   `OutputWatermarker` additionally embeds an invisible zero-width Unicode watermark in
   text outputs so the originating agent can be identified even if the record is lost.

4. **Capability Creep**: Agents accumulate permissions over time; revocations are often
   forgotten or not audited.  `CapabilityLedger` maintains an append-only audit trail of
   every grant and revoke action so the effective permission set is always derivable from
   the immutable history.  `TrustScorer` continuously re-evaluates a composite trust
   score so that an agent whose provenance chain breaks or whose capabilities have
   ballooned beyond their original scope is flagged automatically.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import secrets
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------

@dataclass
class AgentIdentity:
    """Stable, verifiable identity for a registered agent class."""

    agent_id: str
    name: str
    version: str
    code_hash: str  # SHA-256 hex digest of the agent's source code
    public_key: Optional[str]
    created_at: float


# ---------------------------------------------------------------------------
# AgentAttestor
# ---------------------------------------------------------------------------

class AgentAttestor:
    """HMAC-SHA256 attestation for agent source-code integrity.

    The *secret* is used only inside HMAC operations and is never stored in
    any returned object, so leaking an `AgentIdentity` does not expose it.
    """

    def __init__(self, secret: str = "change-me") -> None:
        self._secret: bytes = secret.encode()
        self._registry: dict[str, AgentIdentity] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _source_hash(self, agent_obj: object) -> str:
        """Return SHA-256 hex digest of the agent class source code."""
        src = inspect.getsource(type(agent_obj))
        return hashlib.sha256(src.encode()).hexdigest()

    def _hmac_sign(self, data: str) -> str:
        return hmac.new(self._secret, data.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attest(self, agent_obj: object, version: str = "1.0.0",
               public_key: Optional[str] = None) -> AgentIdentity:
        """Register *agent_obj* and return its `AgentIdentity`.

        A new identity is created each time this is called; re-attesting an
        already-registered agent replaces the previous entry.
        """
        agent_id = self._hmac_sign(secrets.token_hex(16))
        code_hash = self._source_hash(agent_obj)
        identity = AgentIdentity(
            agent_id=agent_id,
            name=type(agent_obj).__name__,
            version=version,
            code_hash=code_hash,
            public_key=public_key,
            created_at=time.time(),
        )
        self._registry[agent_id] = identity
        return identity

    def verify(self, agent_id: str, agent_obj: object) -> bool:
        """Return *True* iff the registered hash matches the current source."""
        identity = self._registry.get(agent_id)
        if identity is None:
            return False
        current_hash = self._source_hash(agent_obj)
        return hmac.compare_digest(identity.code_hash, current_hash)

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        """Return the `AgentIdentity` for *agent_id*, or *None*."""
        return self._registry.get(agent_id)


# ---------------------------------------------------------------------------
# Capability & CapabilityLedger
# ---------------------------------------------------------------------------

@dataclass
class AgentCapability:
    """A named, risk-rated capability that can be granted to an agent."""

    name: str
    description: str
    risk_level: str  # "low" | "medium" | "high" | "critical"


@dataclass
class LedgerEntry:
    """One immutable record in the `CapabilityLedger` audit trail."""

    entry_id: str
    agent_id: str
    capability: AgentCapability
    action: str   # "grant" | "revoke"
    actor: str
    timestamp: float


class CapabilityLedger:
    """Append-only ledger of capability grants and revocations per agent.

    The ledger never deletes records; a revocation is recorded as a new entry
    with ``action="revoke"``.  The effective set of active capabilities is
    derived by replaying the log.
    """

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def grant(self, agent_id: str, capability: AgentCapability,
              granted_by: str) -> LedgerEntry:
        """Append a *grant* entry and return it."""
        entry = LedgerEntry(
            entry_id=secrets.token_hex(16),
            agent_id=agent_id,
            capability=capability,
            action="grant",
            actor=granted_by,
            timestamp=time.time(),
        )
        self._entries.append(entry)
        return entry

    def revoke(self, agent_id: str, capability_name: str,
               revoked_by: str) -> None:
        """Append a *revoke* entry for *capability_name* owned by *agent_id*.

        Raises `KeyError` if the capability was never granted.
        """
        if not self.has(agent_id, capability_name):
            raise KeyError(
                f"Agent '{agent_id}' does not hold capability '{capability_name}'"
            )
        # Find the most recent capability object for the canonical description
        cap_obj: Optional[AgentCapability] = None
        for entry in reversed(self._entries):
            if entry.agent_id == agent_id and entry.capability.name == capability_name:
                cap_obj = entry.capability
                break
        assert cap_obj is not None  # guaranteed by has() check above
        revoke_entry = LedgerEntry(
            entry_id=secrets.token_hex(16),
            agent_id=agent_id,
            capability=cap_obj,
            action="revoke",
            actor=revoked_by,
            timestamp=time.time(),
        )
        self._entries.append(revoke_entry)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has(self, agent_id: str, capability_name: str) -> bool:
        """Return *True* iff the agent currently holds *capability_name*."""
        active = False
        for entry in self._entries:
            if entry.agent_id == agent_id and entry.capability.name == capability_name:
                active = entry.action == "grant"
        return active

    def audit_trail(self, agent_id: str) -> List[LedgerEntry]:
        """Return all ledger entries (in insertion order) for *agent_id*."""
        return [e for e in self._entries if e.agent_id == agent_id]


# ---------------------------------------------------------------------------
# ProvenanceRecord & WorkflowProvenanceChain
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceRecord:
    """A single, HMAC-signed step in the workflow provenance chain."""

    record_id: str
    parent_id: Optional[str]
    agent_id: str
    operation: str
    input_hash: str    # SHA-256 of serialised input data
    output_hash: str   # SHA-256 of serialised output data
    timestamp: float
    signature: str     # HMAC-SHA256 over all other fields


class WorkflowProvenanceChain:
    """Merkle-linked, HMAC-signed chain of provenance records.

    Each record's *parent_id* points to the previous record so the chain can
    be walked back to the root.  `verify_chain()` re-derives every signature
    to detect any in-place tampering.
    """

    def __init__(self, secret: str = "change-me") -> None:
        self._secret: bytes = secret.encode()
        self._records: dict[str, ProvenanceRecord] = {}
        self._insertion_order: list[str] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_data(data: object) -> str:
        serialised = json.dumps(data, sort_keys=True, default=str).encode()
        return hashlib.sha256(serialised).hexdigest()

    def _sign(self, record_id: str, parent_id: Optional[str], agent_id: str,
              operation: str, input_hash: str, output_hash: str,
              timestamp: float) -> str:
        payload = json.dumps({
            "record_id": record_id,
            "parent_id": parent_id,
            "agent_id": agent_id,
            "operation": operation,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "timestamp": timestamp,
        }, sort_keys=True)
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, agent_id: str, operation: str, input_data: object,
               output_data: object, parent_id: Optional[str] = None) -> ProvenanceRecord:
        """Append a new provenance record and return it."""
        record_id = secrets.token_hex(16)
        input_hash = self._hash_data(input_data)
        output_hash = self._hash_data(output_data)
        timestamp = time.time()
        signature = self._sign(
            record_id, parent_id, agent_id, operation,
            input_hash, output_hash, timestamp,
        )
        rec = ProvenanceRecord(
            record_id=record_id,
            parent_id=parent_id,
            agent_id=agent_id,
            operation=operation,
            input_hash=input_hash,
            output_hash=output_hash,
            timestamp=timestamp,
            signature=signature,
        )
        self._records[record_id] = rec
        self._insertion_order.append(record_id)
        return rec

    def verify_chain(self) -> bool:
        """Re-derive all HMAC signatures and verify parent links.

        Returns *True* iff every record is intact and all parent references
        resolve correctly.
        """
        for rid in self._insertion_order:
            rec = self._records[rid]
            # Check parent exists (if set)
            if rec.parent_id is not None and rec.parent_id not in self._records:
                return False
            # Re-derive expected signature
            expected = self._sign(
                rec.record_id, rec.parent_id, rec.agent_id, rec.operation,
                rec.input_hash, rec.output_hash, rec.timestamp,
            )
            if not hmac.compare_digest(rec.signature, expected):
                return False
        return True

    def get_lineage(self, record_id: str) -> List[ProvenanceRecord]:
        """Walk the parent chain from *record_id* to the root.

        Returns records ordered from *record_id* up to the root (oldest last).
        Raises `KeyError` if *record_id* is unknown.
        """
        if record_id not in self._records:
            raise KeyError(f"Unknown record_id: '{record_id}'")
        lineage: list[ProvenanceRecord] = []
        current_id: Optional[str] = record_id
        visited: set[str] = set()
        while current_id is not None:
            if current_id in visited:
                # Cycle guard — should never happen with a correct chain
                break
            visited.add(current_id)
            rec = self._records.get(current_id)
            if rec is None:
                break
            lineage.append(rec)
            current_id = rec.parent_id
        return lineage


# ---------------------------------------------------------------------------
# OutputWatermarker
# ---------------------------------------------------------------------------

# Zero-width characters used to encode bits
_ZW_ZERO = "\u200b"   # ZERO WIDTH SPACE          → bit 0
_ZW_ONE = "\u200c"    # ZERO WIDTH NON-JOINER     → bit 1
_ZW_SEP = "\u200d"    # ZERO WIDTH JOINER         → separator between bytes

# Sentinels use distinct character sequences so they cannot collide with payload:
#   START = ZW_SEP ZW_ZERO ZW_SEP  (joiner + space + joiner — never appears in payload
#           because payload bytes are separated by ZW_SEP alone, not ZW_SEP+ZW_ZERO+ZW_SEP)
#   END   = ZW_SEP ZW_ONE  ZW_SEP
_WM_START = _ZW_SEP + _ZW_ZERO + _ZW_SEP
_WM_END = _ZW_SEP + _ZW_ONE + _ZW_SEP


@dataclass
class WatermarkPayload:
    """A watermarked text output together with its provenance metadata."""

    content: str          # watermarked text (contains invisible characters)
    watermark_id: str     # the ID that was encoded
    agent_id: str
    timestamp: float


class OutputWatermarker:
    """Embeds and extracts invisible zero-width Unicode watermarks in text.

    Encoding scheme
    ---------------
    The *watermark_id* (a hex string) is UTF-8 encoded, then each byte is
    represented as 8 bits using ``_ZW_ZERO`` (bit 0) and ``_ZW_ONE`` (bit 1),
    with ``_ZW_SEP`` inserted between bytes.  The entire bit-string is injected
    immediately after the first word of the text.

    The payload is bounded by distinct sentinel sequences:
      ``_WM_START`` … encoded payload … ``_WM_END``

    The sentinels contain ``_ZW_ZERO`` / ``_ZW_ONE`` flanked by ``_ZW_SEP`` which
    creates 3-character sequences that cannot appear inside the payload (where
    ``_ZW_SEP`` only occurs as a single byte-separator, never adjacent to another
    ``_ZW_SEP``).
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_id(watermark_id: str) -> str:
        """Encode *watermark_id* bytes as zero-width characters."""
        parts: list[str] = []
        for byte_val in watermark_id.encode():
            bits = format(byte_val, "08b")
            zw_bits = "".join(_ZW_ONE if b == "1" else _ZW_ZERO for b in bits)
            parts.append(zw_bits)
        return _ZW_SEP.join(parts)

    @staticmethod
    def _decode_id(zw_str: str) -> Optional[str]:
        """Decode a zero-width string back to the original *watermark_id*."""
        byte_parts = zw_str.split(_ZW_SEP)
        result_bytes: list[int] = []
        for part in byte_parts:
            if len(part) != 8:
                return None
            try:
                bit_str = "".join("1" if ch == _ZW_ONE else "0" for ch in part)
                result_bytes.append(int(bit_str, 2))
            except ValueError:
                return None
        try:
            return bytes(result_bytes).decode()
        except (UnicodeDecodeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def watermark(self, text: str, agent_id: str) -> WatermarkPayload:
        """Return a `WatermarkPayload` whose `.content` carries an invisible mark."""
        watermark_id = secrets.token_hex(8)
        encoded = self._encode_id(watermark_id)
        injection = _WM_START + encoded + _WM_END

        # Inject after the first whitespace-delimited word
        words = text.split(" ", 1)
        if len(words) == 1:
            watermarked = words[0] + injection
        else:
            watermarked = words[0] + injection + " " + words[1]

        return WatermarkPayload(
            content=watermarked,
            watermark_id=watermark_id,
            agent_id=agent_id,
            timestamp=time.time(),
        )

    def extract(self, text: str) -> Optional[str]:
        """Return the decoded watermark_id embedded in *text*, or *None*."""
        start = text.find(_WM_START)
        if start == -1:
            return None
        inner_start = start + len(_WM_START)
        end = text.find(_WM_END, inner_start)
        if end == -1:
            return None
        zw_payload = text[inner_start:end]
        return self._decode_id(zw_payload)

    def verify(self, payload: WatermarkPayload) -> bool:
        """Return *True* iff the watermark in *payload.content* matches *payload.watermark_id*."""
        extracted = self.extract(payload.content)
        if extracted is None:
            return False
        return hmac.compare_digest(extracted, payload.watermark_id)


# ---------------------------------------------------------------------------
# TrustScore & TrustScorer
# ---------------------------------------------------------------------------

@dataclass
class TrustScore:
    """Composite trust score for an agent, broken down by component."""

    agent_id: str
    score: float               # weighted aggregate in [0, 1]
    components: Dict[str, float]
    computed_at: float


class TrustScorer:
    """Computes a weighted composite trust score from four independent signals.

    Component weights
    -----------------
    +----------------------+--------+
    | Component            | Weight |
    +----------------------+--------+
    | attestation_valid    |  0.30  |
    | capability_compliance|  0.30  |
    | provenance_intact    |  0.20  |
    | behavioral_history   |  0.20  |
    +----------------------+--------+

    ``behavioral_history`` is currently approximated as the fraction of
    provenance records attributed to *agent_id* whose signatures are valid
    (i.e. the agent has a clean operational history in the chain).
    """

    _WEIGHTS: dict[str, float] = {
        "attestation_valid": 0.30,
        "capability_compliance": 0.30,
        "provenance_intact": 0.20,
        "behavioral_history": 0.20,
    }

    # Risk-level penalty applied when high-risk capabilities are present
    _RISK_PENALTY: dict[str, float] = {
        "low": 0.0,
        "medium": 0.05,
        "high": 0.15,
        "critical": 0.35,
    }

    def score(self, agent_id: str, attester: AgentAttestor,
              ledger: CapabilityLedger, chain: WorkflowProvenanceChain) -> TrustScore:
        """Compute and return the `TrustScore` for *agent_id*."""

        # --- attestation_valid (0.30) ---
        identity = attester.get(agent_id)
        attestation_score = 1.0 if identity is not None else 0.0

        # --- capability_compliance (0.30) ---
        # Penalise for each active high-risk or critical capability
        trail = ledger.audit_trail(agent_id)
        active_caps: dict[str, AgentCapability] = {}
        for entry in trail:
            if entry.action == "grant":
                active_caps[entry.capability.name] = entry.capability
            elif entry.capability.name in active_caps:
                del active_caps[entry.capability.name]

        capability_score = 1.0
        for cap in active_caps.values():
            capability_score -= self._RISK_PENALTY.get(cap.risk_level, 0.0)
        capability_score = max(0.0, capability_score)

        # --- provenance_intact (0.20) ---
        provenance_score = 1.0 if chain.verify_chain() else 0.0

        # --- behavioral_history (0.20) ---
        # Fraction of this agent's records that have valid signatures
        agent_records = [
            chain._records[rid]
            for rid in chain._insertion_order
            if chain._records[rid].agent_id == agent_id
        ]
        if not agent_records:
            behavioral_score = 1.0  # no history — benefit of the doubt
        else:
            valid_count = 0
            for rec in agent_records:
                expected = chain._sign(
                    rec.record_id, rec.parent_id, rec.agent_id, rec.operation,
                    rec.input_hash, rec.output_hash, rec.timestamp,
                )
                if hmac.compare_digest(rec.signature, expected):
                    valid_count += 1
            behavioral_score = valid_count / len(agent_records)

        components = {
            "attestation_valid": attestation_score,
            "capability_compliance": capability_score,
            "provenance_intact": provenance_score,
            "behavioral_history": behavioral_score,
        }
        aggregate = sum(
            components[k] * self._WEIGHTS[k] for k in self._WEIGHTS
        )
        return TrustScore(
            agent_id=agent_id,
            score=round(aggregate, 6),
            components=components,
            computed_at=time.time(),
        )


# ---------------------------------------------------------------------------
# AgentTrustManager — high-level facade
# ---------------------------------------------------------------------------

class AgentTrustManager:
    """Convenience facade that wires all trust sub-systems together.

    Parameters
    ----------
    secret:
        Shared HMAC secret used by `AgentAttestor` and
        `WorkflowProvenanceChain`.  **Change this in production.**
    """

    def __init__(self, secret: str = "change-me") -> None:
        self.attestor = AgentAttestor(secret=secret)
        self.ledger = CapabilityLedger()
        self.chain = WorkflowProvenanceChain(secret=secret)
        self.watermarker = OutputWatermarker()
        self.scorer = TrustScorer()

    def trust_score(self, agent_id: str) -> TrustScore:
        """Shortcut: compute trust score for *agent_id* using all sub-systems."""
        return self.scorer.score(agent_id, self.attestor, self.ledger, self.chain)


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    "AgentIdentity",
    "AgentAttestor",
    "AgentCapability",
    "LedgerEntry",
    "CapabilityLedger",
    "ProvenanceRecord",
    "WorkflowProvenanceChain",
    "WatermarkPayload",
    "OutputWatermarker",
    "TrustScore",
    "TrustScorer",
    "AgentTrustManager",
]
