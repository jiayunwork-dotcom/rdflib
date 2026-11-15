"""Issued-identifier-map conformance checks (RDFC-1.0 ``RDFC10MapTest``).

The public API only returns the canonical document, but the algorithm builds
the issued-identifiers map internally.  These tests reach into the private
canonicalizer to compare that map with the W3C expected JSON, which is stronger
evidence than the document alone: two different label assignments could
serialize identically for a symmetric dataset, and the map test would catch it.

RDFLib's N-Quads parser replaces document blank node labels with fresh
identifiers, so the *keys* of the map are not the labels used in the test
document.  The test README says keys are not considered; what matters is that
the input blank nodes map one-to-one onto the expected canonical labels.  The
comparison below therefore checks the induced bijection.
"""

from __future__ import annotations

import json
from typing import Dict, List

import pytest

import rdflib
import rdflib.compare as compare_module
from rdflib import BNode
from rdflib.graph import Dataset
from test.data import TEST_DATA_DIR

SUITE_DIR = TEST_DATA_DIR / "suites" / "w3c" / "rdf-canon"
MANIFEST = json.loads((SUITE_DIR / "manifest-pinned.json").read_text("utf-8"))

ALL_MAP_TESTS = [
    entry for entry in MANIFEST["entries"] if entry["type"] == "rdfc:RDFC10MapTest"
]
#: SHA-384 is out of scope for this API (see test_rdfc_canonicalize.py).
MAP_TESTS = [
    entry
    for entry in ALL_MAP_TESTS
    if (entry.get("hashAlgorithm") or "SHA256") == "SHA256"
]

#: Document label -> RDFLib BNode, captured while parsing so the expected keys
#: ("e0", "g1", ...) can be matched to the identifiers actually stored.
_BNODE_CONTEXT: Dict[str, BNode] = {}


def _parse_preserving_lexical_forms(path) -> Dataset:
    dataset = Dataset()
    previous = rdflib.NORMALIZE_LITERALS
    rdflib.NORMALIZE_LITERALS = False
    try:
        dataset.parse(path, format="nquads", bnode_context=_BNODE_CONTEXT)
    finally:
        rdflib.NORMALIZE_LITERALS = previous
    return dataset


def _issued_map(dataset: Dataset) -> Dict[str, str]:
    quads = compare_module._rdfc_collect_quads(dataset)
    for quad in quads:
        compare_module._rdfc_validate_quad(quad)
    canonicalizer = compare_module._RDFCDatasetCanonicalizer(quads)
    canonicalizer.canonicalize()
    return dict(canonicalizer.canonical_issuer.issued)


def test_map_manifest_coverage() -> None:
    assert len(ALL_MAP_TESTS) == 21
    assert len(MAP_TESTS) == 20
    excluded = {entry["id"] for entry in ALL_MAP_TESTS} - {
        entry["id"] for entry in MAP_TESTS
    }
    assert excluded == {"#test075m"}


@pytest.mark.parametrize("entry", MAP_TESTS, ids=[e["id"] for e in MAP_TESTS])
def test_issued_identifier_map(entry: Dict[str, str]) -> None:
    _BNODE_CONTEXT.clear()
    dataset = _parse_preserving_lexical_forms(SUITE_DIR / entry["action"])
    actual = _issued_map(dataset)
    expected: Dict[str, str] = json.loads(
        (SUITE_DIR / entry["result"]).read_text("utf-8")
    )

    # Translate the expected document labels to the parsed node identifiers.
    assert set(expected) == set(
        _BNODE_CONTEXT
    ), "expected map keys and parsed blank nodes disagree"
    expected_by_node = {
        str(_BNODE_CONTEXT[label]): canonical for label, canonical in expected.items()
    }
    assert actual == expected_by_node

    # The labels must also form a complete, distinct 0-based sequence.
    labels: List[int] = sorted(int(value[len("c14n") :]) for value in actual.values())
    assert labels == list(range(len(labels)))
