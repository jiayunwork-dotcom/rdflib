"""Scale and complexity tests for RDFC-1.0 canonicalization.

These run two representative datasets:

* 50,000 quads with no blank nodes, spread across the default graph and
  several IRI-named graphs.  This exercises the plain path (first-degree
  hashes are unique, no N-degree hashing at all).
* 2,000 *asymmetric* blank nodes: an uneven chain with a skip edge and a
  unique literal label on every node.  Every blank node has a distinct
  first-degree hash, so this measures the per-node overhead rather than
  the permutation search.

Both datasets are generated deterministically, and the recorded timings are
checked only against very loose upper bounds so the tests are stable on slow
machines.  The exact numbers measured on the reference machine are in
``benchmark_rdfc.py``'s docstring; re-run it with ``python benchmark_rdfc.py``
from this directory to reproduce them.
"""

from __future__ import annotations

import hashlib
import time

import pytest

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.compare import canonicalize_dataset

EX = "http://example.org/"


def _no_blank_nodes(count: int = 50000) -> Dataset:
    dataset = Dataset()
    for index in range(count):
        graph = URIRef(f"{EX}graph/{index % 7}")
        dataset.add(
            (
                URIRef(f"{EX}s/{index}"),
                URIRef(f"{EX}p/{index % 13}"),
                Literal(f"value {index} é中"),
                graph,
            )
        )
    return dataset


def _asymmetric_blank_nodes(count: int = 2000) -> Dataset:
    dataset = Dataset()
    for index in range(count):
        node = BNode(f"b{index}")
        if index + 1 < count:
            dataset.add((node, URIRef(f"{EX}next"), BNode(f"b{index + 1}")))
        if index % 3 == 0 and index + 7 < count:
            dataset.add((node, URIRef(f"{EX}skip"), BNode(f"b{index + 7}")))
        dataset.add((node, URIRef(f"{EX}label"), Literal(f"node-{index:05d}")))
    return dataset


def _time(dataset: Dataset) -> tuple[bytes, float]:
    start = time.perf_counter()
    output = canonicalize_dataset(dataset)
    return output, time.perf_counter() - start


def test_50000_quads_without_blank_nodes() -> None:
    dataset = _no_blank_nodes()
    output, seconds = _time(dataset)
    assert output.count(b"\n") == 50000
    assert hashlib.sha256(output).hexdigest()
    # Measured at ~1.2 s on the reference machine; allow a lot of slack.
    assert seconds < 30.0


def test_2000_asymmetric_blank_nodes() -> None:
    dataset = _asymmetric_blank_nodes()
    output, seconds = _time(dataset)
    assert output.count(b"\n") > 2000
    assert b"_:c14n0" in output
    # Measured at ~0.06 s on the reference machine.
    assert seconds < 30.0


@pytest.mark.parametrize("size", [8, 9, 10])
def test_highly_symmetric_input_is_bounded(size: int) -> None:
    """A complete graph of blank nodes ("clique") must fail fast, not hang.

    This is the shape of the W3C poison vector.  The exact threshold is an
    implementation detail; what matters is that the call returns in bounded
    time with a clear error instead of exploring a factorial number of
    permutations.
    """
    from rdflib.compare import RDFCanonicalizationError

    dataset = Dataset()
    nodes = [BNode(f"b{index}") for index in range(size)]
    predicate = URIRef(f"{EX}p")
    for source in nodes:
        for target in nodes:
            dataset.add((source, predicate, target))
    start = time.perf_counter()
    with pytest.raises(RDFCanonicalizationError):
        canonicalize_dataset(dataset)
    assert time.perf_counter() - start < 30.0
