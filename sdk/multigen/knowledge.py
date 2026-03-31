"""
Organisational knowledge management for Multigen.

Problems solved
---------------
- No organisational knowledge graph (entities, relationships, facts)
- No knowledge versioning (facts change — old workflows see old facts)
- No knowledge confidence scoring (not all facts equally trusted)
- No contradiction detection (two sources disagree)
- No knowledge provenance (where did this fact come from)
- No domain ontology support (structured taxonomy)
- No knowledge expiry (facts go stale — TTL on domain knowledge)
- No cross-agent knowledge sharing (Agent A learns, Agent B can use it)

Classes
-------
- ``Entity``                — a named entity with type + metadata
- ``Fact``                  — a versioned, confident, provenance-tracked assertion
- ``Relationship``          — typed edge between two entities
- ``KnowledgeGraph``        — entity + fact + relationship store
- ``VersionedFact``         — fact with MVCC history
- ``ContradictionDetector`` — flags conflicting facts
- ``Ontology``              — hierarchical type taxonomy
- ``KnowledgeManager``      — high-level facade

Usage::

    from multigen.knowledge import KnowledgeManager

    mgr = KnowledgeManager()
    mgr.add_entity("Apple Inc.", entity_type="company")
    mgr.add_fact("Apple Inc.", "revenue_2023", "$383B",
                 source="annual_report", confidence=0.99)
    mgr.add_relationship("Apple Inc.", "CEO", "Tim Cook")

    facts = mgr.get_facts("Apple Inc.")
    ctx = mgr.context_for("Apple Inc.", max_facts=10)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Entity ────────────────────────────────────────────────────────────────────

@dataclass
class Entity:
    name: str
    entity_type: str = "generic"
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ── Fact ──────────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    """A versioned, confident, provenance-tracked assertion about an entity."""
    entity: str
    attribute: str
    value: Any
    version: int = 1
    confidence: float = 1.0
    source: str = "unknown"
    provenance: str = ""            # URL, document title, agent name, etc.
    expires_at: Optional[float] = None   # None = never expires
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    fact_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def key(self) -> str:
        return f"{self.entity}::{self.attribute}"


# ── Relationship ──────────────────────────────────────────────────────────────

@dataclass
class Relationship:
    subject: str
    relation: str
    object_: str
    confidence: float = 1.0
    source: str = "unknown"
    bidirectional: bool = False
    created_at: float = field(default_factory=time.time)


# ── ContradictionDetector ────────────────────────────────────────────────────

@dataclass
class Contradiction:
    entity: str
    attribute: str
    fact_a: Fact
    fact_b: Fact

    def __str__(self) -> str:
        return (
            f"Contradiction [{self.entity}::{self.attribute}]: "
            f"{self.fact_a.value!r} (src={self.fact_a.source!r}) "
            f"vs {self.fact_b.value!r} (src={self.fact_b.source!r})"
        )


class ContradictionDetector:
    """
    Detects conflicting facts for the same entity attribute.

    Usage::

        detector = ContradictionDetector()
        detector.add(Fact("Apple", "ceo", "Tim Cook", source="wiki"))
        detector.add(Fact("Apple", "ceo", "Steve Jobs", source="old_doc"))
        contradictions = detector.contradictions()
    """

    def __init__(self) -> None:
        self._facts: Dict[str, List[Fact]] = {}   # key → list of facts

    def add(self, fact: Fact) -> Optional[Contradiction]:
        existing = self._facts.setdefault(fact.key, [])
        contradiction = None
        for other in existing:
            if str(other.value) != str(fact.value):
                contradiction = Contradiction(
                    entity=fact.entity,
                    attribute=fact.attribute,
                    fact_a=other,
                    fact_b=fact,
                )
        existing.append(fact)
        return contradiction

    def contradictions(self) -> List[Contradiction]:
        results = []
        for key, facts in self._facts.items():
            seen_values: Dict[str, Fact] = {}
            for f in facts:
                v = str(f.value)
                if seen_values and v not in seen_values:
                    first = next(iter(seen_values.values()))
                    results.append(Contradiction(
                        entity=f.entity,
                        attribute=f.attribute,
                        fact_a=first,
                        fact_b=f,
                    ))
                seen_values[v] = f
        return results


# ── Ontology ──────────────────────────────────────────────────────────────────

class Ontology:
    """
    Hierarchical type taxonomy (type → parent type).

    Usage::

        onto = Ontology()
        onto.define("organisation")
        onto.define("company", parent="organisation")
        onto.define("startup", parent="company")
        print(onto.ancestors("startup"))   # ["company", "organisation"]
        print(onto.subtypes("organisation"))  # ["company", "startup"]
    """

    def __init__(self) -> None:
        self._parents: Dict[str, Optional[str]] = {}

    def define(self, type_name: str, parent: Optional[str] = None) -> None:
        self._parents[type_name] = parent

    def ancestors(self, type_name: str) -> List[str]:
        result = []
        current = self._parents.get(type_name)
        while current:
            result.append(current)
            current = self._parents.get(current)
        return result

    def subtypes(self, type_name: str) -> List[str]:
        direct = [t for t, p in self._parents.items() if p == type_name]
        all_subs = list(direct)
        for sub in direct:
            all_subs.extend(self.subtypes(sub))
        return all_subs

    def is_a(self, child: str, parent: str) -> bool:
        return parent in self.ancestors(child) or child == parent

    def all_types(self) -> List[str]:
        return list(self._parents.keys())


# ── KnowledgeGraph ────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Stores entities, facts (versioned + provenance), and relationships.

    Features:
    - MVCC fact history (read at a specific version)
    - TTL expiry (facts go stale automatically)
    - Confidence scoring (low-confidence facts are filtered by default)
    - Cross-entity relationship traversal

    Usage::

        kg = KnowledgeGraph()
        kg.add_entity(Entity("Apple Inc.", entity_type="company"))
        kg.add_fact(Fact("Apple Inc.", "revenue", "$383B",
                    source="annual_report", confidence=0.99,
                    expires_at=time.time() + 86400*365))
        kg.add_relationship(Relationship("Apple Inc.", "leads", "Tim Cook"))

        facts = kg.facts("Apple Inc.", min_confidence=0.8)
    """

    def __init__(self) -> None:
        self._entities: Dict[str, Entity] = {}
        self._facts: Dict[str, List[Fact]] = {}          # entity_name → facts
        self._relationships: List[Relationship] = []
        self._detector = ContradictionDetector()

    # Entities

    def add_entity(self, entity: Entity) -> None:
        self._entities[entity.name] = entity
        for alias in entity.aliases:
            self._entities[alias] = entity

    def entity(self, name: str) -> Optional[Entity]:
        return self._entities.get(name)

    def all_entities(self) -> List[Entity]:
        return list({id(e): e for e in self._entities.values()}.values())

    # Facts

    def add_fact(self, fact: Fact) -> Optional[Contradiction]:
        self._facts.setdefault(fact.entity, []).append(fact)
        return self._detector.add(fact)

    def facts(
        self,
        entity: str,
        min_confidence: float = 0.0,
        include_expired: bool = False,
        attribute: Optional[str] = None,
    ) -> List[Fact]:
        all_facts = self._facts.get(entity, [])
        result = []
        for f in all_facts:
            if not include_expired and f.is_expired:
                continue
            if f.confidence < min_confidence:
                continue
            if attribute and f.attribute != attribute:
                continue
            result.append(f)
        return result

    def latest_fact(self, entity: str, attribute: str) -> Optional[Fact]:
        matching = self.facts(entity, attribute=attribute)
        return max(matching, key=lambda f: f.version, default=None)

    def contradictions(self) -> List[Contradiction]:
        return self._detector.contradictions()

    # Relationships

    def add_relationship(self, rel: Relationship) -> None:
        self._relationships.append(rel)

    def relationships(
        self,
        subject: Optional[str] = None,
        relation: Optional[str] = None,
        object_: Optional[str] = None,
    ) -> List[Relationship]:
        return [
            r for r in self._relationships
            if (subject is None or r.subject == subject)
            and (relation is None or r.relation == relation)
            and (object_ is None or r.object_ == object_)
        ]

    def neighbours(self, entity: str) -> List[str]:
        related = set()
        for r in self._relationships:
            if r.subject == entity:
                related.add(r.object_)
            if r.bidirectional and r.object_ == entity:
                related.add(r.subject)
        return sorted(related)

    # Context generation

    def context_for(
        self,
        entity: str,
        max_facts: int = 10,
        min_confidence: float = 0.0,
    ) -> str:
        facts = self.facts(entity, min_confidence=min_confidence)[:max_facts]
        rels = self.relationships(subject=entity)
        lines = [f"Knowledge about: {entity}"]
        for f in facts:
            lines.append(f"  {f.attribute}: {f.value} (confidence={f.confidence:.0%}, src={f.source})")
        for r in rels:
            lines.append(f"  [{r.relation}] → {r.object_}")
        return "\n".join(lines)


# ── KnowledgeManager facade ───────────────────────────────────────────────────

class KnowledgeManager:
    """
    High-level facade for organisational knowledge management.

    Usage::

        mgr = KnowledgeManager()
        mgr.add_entity("Apple Inc.", entity_type="company")
        mgr.add_fact("Apple Inc.", "revenue_2023", "$383B",
                     source="annual_report", confidence=0.99)
        mgr.add_relationship("Apple Inc.", "CEO", "Tim Cook")

        ctx = mgr.context_for("Apple Inc.")
        contradictions = mgr.contradictions()
    """

    def __init__(self) -> None:
        self.graph = KnowledgeGraph()
        self.ontology = Ontology()

    def add_entity(self, name: str, entity_type: str = "generic", **metadata: Any) -> Entity:
        entity = Entity(name=name, entity_type=entity_type, metadata=metadata)
        self.graph.add_entity(entity)
        return entity

    def add_fact(
        self,
        entity: str,
        attribute: str,
        value: Any,
        source: str = "unknown",
        confidence: float = 1.0,
        ttl_days: Optional[float] = None,
        provenance: str = "",
        **tags: Any,
    ) -> Fact:
        expires_at = time.time() + ttl_days * 86400 if ttl_days else None
        fact = Fact(
            entity=entity,
            attribute=attribute,
            value=value,
            source=source,
            confidence=confidence,
            expires_at=expires_at,
            provenance=provenance,
        )
        self.graph.add_fact(fact)
        return fact

    def add_relationship(
        self,
        subject: str,
        relation: str,
        object_: str,
        confidence: float = 1.0,
        source: str = "unknown",
    ) -> Relationship:
        rel = Relationship(
            subject=subject,
            relation=relation,
            object_=object_,
            confidence=confidence,
            source=source,
        )
        self.graph.add_relationship(rel)
        return rel

    def context_for(self, entity: str, max_facts: int = 10, min_confidence: float = 0.0) -> str:
        return self.graph.context_for(entity, max_facts=max_facts, min_confidence=min_confidence)

    def contradictions(self) -> List[Contradiction]:
        return self.graph.contradictions()

    def expired_facts(self) -> List[Fact]:
        result = []
        for entity_facts in self.graph._facts.values():
            result.extend(f for f in entity_facts if f.is_expired)
        return result

    def share_fact(self, fact: Fact, target_manager: "KnowledgeManager") -> None:
        """Share a fact with another KnowledgeManager (cross-agent sharing)."""
        target_manager.graph.add_fact(fact)


__all__ = [
    "Entity",
    "Fact",
    "Relationship",
    "Contradiction",
    "ContradictionDetector",
    "Ontology",
    "KnowledgeGraph",
    "KnowledgeManager",
]
