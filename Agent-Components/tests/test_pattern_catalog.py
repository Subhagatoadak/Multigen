"""Tests for the pattern catalog metadata."""
from __future__ import annotations

from agentic_codex.patterns import list_categories, list_patterns


def test_catalog_categories_present() -> None:
    categories = list_categories()
    assert "hierarchical" in categories
    assert "collective_intelligence" in categories
    assert len(categories) >= 7


def test_list_patterns_returns_descriptors() -> None:
    hierarchical = list_patterns("hierarchical")
    descriptor = hierarchical[0]
    assert descriptor.name == "Ministry of Experts"
    assert "Minister" in descriptor.personas[0]
    entire_catalog = list_patterns()
    assert "market" in entire_catalog
