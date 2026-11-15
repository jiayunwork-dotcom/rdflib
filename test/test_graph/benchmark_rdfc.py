"""Reproducible scale measurements for RDFC-1.0 canonicalization.

Run from the repository root::

    python test/test_graph/benchmark_rdfc.py

It prints one JSON object per dataset with the number of quads, the output
size, the SHA-256 of the complete canonical output, the wall-clock time and
the peak resident set size of the process.  The two datasets are the same as
in ``test_rdfc_scale.py``.

Reference numbers from a Linux container, CPython 3.11.2, ``rdflib`` from this
working tree (taken 2026-11-15, three consecutive runs):

======================  ======  =========  ===========  ==========  =============
dataset                 quads   bytes      seconds      peak RSS    output SHA-256
======================  ======  =========  ===========  ==========  =============
50000-no-bnodes         50000   5239318    1.22 - 1.35  178.5 MiB   4d37465e...
2000-asymmetric-bnodes  4664    235137     0.055 - 0.10 178.5 MiB   0371ecce...
======================  ======  =========  ===========  ==========  =============

Peak RSS is for the whole process and includes importing RDFLib and parsing
the input; the canonicalization step itself adds little on top.

Applicability of RDFC-1.0 is limited by its own complexity: section 7.1 of
the Recommendation acknowledges datasets that cannot be canonicalized in
reasonable time.  Highly symmetric inputs (cliques, large unlabelled cycles)
need a number of N-degree hashes that grows quadratically or worse; this
implementation caps that work and raises ``RDFCanonicalizationError`` rather
than running unboundedly.
"""

from __future__ import annotations

import hashlib
import json
import resource
import time
from typing import Any, Dict

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.compare import canonicalize_dataset

EX = "http://example.org/"


def _no_blank_nodes(count: int = 50000) -> Dataset:
    dataset = Dataset()
    for index in range(count):
        dataset.add(
            (
                URIRef(f"{EX}s/{index}"),
                URIRef(f"{EX}p/{index % 13}"),
                Literal(f"value {index} é中"),
                URIRef(f"{EX}graph/{index % 7}"),
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


def _measure(label: str, dataset: Dataset) -> Dict[str, Any]:
    quad_count = sum(1 for _ in dataset.quads((None, None, None, None)))
    start = time.perf_counter()
    output = canonicalize_dataset(dataset)
    seconds = time.perf_counter() - start
    peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    return {
        "dataset": label,
        "quads": quad_count,
        "bytes": len(output),
        "lines": output.count(b"\n"),
        "sha256": hashlib.sha256(output).hexdigest(),
        "seconds": round(seconds, 3),
        "peak_rss_mib": round(peak_mib, 1),
    }


def main() -> None:
    for label, dataset in (
        ("50000-no-bnodes", _no_blank_nodes()),
        ("2000-asymmetric-bnodes", _asymmetric_blank_nodes()),
    ):
        print(json.dumps(_measure(label, dataset), ensure_ascii=False))


if __name__ == "__main__":
    main()
