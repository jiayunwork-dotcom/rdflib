"""Tests for RDFC-1.0 canonical dataset serialization.

The W3C evaluation vectors run from the vendored snapshot in
``test/data/suites/w3c/rdf-canon`` (see ``manifest-pinned.json`` for the exact
upstream commit).  The remaining tests are hand-written datasets covering the
behaviour the standard leaves to the implementation: cross-graph blank node
sharing, graph names that are also ordinary nodes, default/named duplication,
escaping, and argument validation.
"""

from __future__ import annotations

import json
import pathlib
from io import BytesIO
from typing import Any, List, Optional, Tuple

import pytest

import rdflib
from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.compare import RDFCanonicalizationError, canonicalize_dataset
from rdflib.namespace import XSD
from test.data import TEST_DATA_DIR

SUITE_DIR = TEST_DATA_DIR / "suites" / "w3c" / "rdf-canon"
VECTORS_DIR = SUITE_DIR / "rdfc10"
MANIFEST = json.loads((SUITE_DIR / "manifest-pinned.json").read_text("utf-8"))

#: The hash algorithm this implementation exposes is SHA-256 only.  Vectors
#: parameterised for SHA-384 are therefore out of scope, as are vectors that
#: only check the issued-identifier map (RDFC10MapTest) rather than the
#: canonical document, and URGNA2012 vectors (not part of this suite).
APPLICABLE_TYPES = {"rdfc:RDFC10EvalTest"}


def _vector_ids() -> List[str]:
    return [
        entry["id"]
        for entry in MANIFEST["entries"]
        if entry["type"] in APPLICABLE_TYPES
        and (entry.get("hashAlgorithm") or "SHA256") == "SHA256"
    ]


def _load_quad_strings(path: pathlib.Path) -> List[str]:
    """Read an N-Quads file as text, splitting on LF only.

    ``str.splitlines`` also splits on U+0085 and friends, which would corrupt
    the control-character vector; canonical N-Quads always uses LF.
    """
    text = path.read_bytes().decode("utf-8")
    return [line for line in text.split("\n") if line]


def _dataset_from_nquads(path: pathlib.Path) -> Dataset:
    """Parse N-Quads while preserving literal lexical forms.

    RDFLib normalizes recognized datatypes at construction time by default
    (``"1.23E0"^^xsd:double`` becomes ``"1.23"``).  RDFC-1.0 canonicalizes the
    terms as given, so the vectors that exercise lexical forms are only
    meaningful with ``NORMALIZE_LITERALS`` disabled.  The parser has no per-call
    switch, so the flag is toggled around the parse and restored afterwards.
    """
    previous = rdflib.NORMALIZE_LITERALS
    rdflib.NORMALIZE_LITERALS = False
    try:
        dataset = Dataset()
        dataset.parse(path, format="nquads")
    finally:
        rdflib.NORMALIZE_LITERALS = previous
    return dataset


@pytest.mark.parametrize("vector_id", _vector_ids())
def test_w3c_rdfc10_evaluation_vectors(vector_id: str) -> None:
    """Every applicable W3C RDFC-1.0 evaluation vector, byte for byte."""
    entry = next(e for e in MANIFEST["entries"] if e["id"] == vector_id)
    action = SUITE_DIR / entry["action"]
    result = SUITE_DIR / entry["result"]
    dataset = _dataset_from_nquads(action)
    expected = result.read_bytes()
    actual = canonicalize_dataset(dataset)
    assert actual == expected


def test_w3c_vector_coverage_is_explicit() -> None:
    """The suite is pinned and every exclusion is deliberate.

    86 manifest entries: 64 evaluation vectors, 21 issued-identifier-map
    tests, 1 negative evaluation test.  Of the 64 evaluation vectors, 63 use
    SHA-256 (which is what this API exposes) and one (test075c) is
    parameterized for SHA-384.  The map tests are verified separately in
    ``test_rdfc_identifier_map.py``; only the SHA-384 map test is excluded.
    """
    entries = MANIFEST["entries"]
    eval_vectors = [e for e in entries if e["type"] == "rdfc:RDFC10EvalTest"]
    sha384 = [e for e in eval_vectors if e.get("hashAlgorithm") == "SHA384"]
    negative = [e for e in entries if e["type"] == "rdfc:RDFC10NegativeEvalTest"]
    map_tests = [e for e in entries if e["type"] == "rdfc:RDFC10MapTest"]

    assert len(entries) == 86
    assert len(eval_vectors) == 64
    assert len(map_tests) == 21
    assert [e["id"] for e in sha384] == ["#test075c"]
    assert [e["id"] for e in negative] == ["#test074c"]

    used = set(_vector_ids())
    assert len(used) == 63
    assert "#test075c" not in used
    # Every evaluation vector except the SHA-384 one is present and applicable.
    assert used == {e["id"] for e in eval_vectors if e["id"] != "#test075c"}


def test_w3c_poison_vector_is_rejected() -> None:
    """The poison vector must raise, not hang (RDFC-1.0 section 7.1)."""
    dataset = _dataset_from_nquads(VECTORS_DIR / "test074-in.nq")
    with pytest.raises(RDFCanonicalizationError):
        canonicalize_dataset(dataset)


# ---------------------------------------------------------------------------
# Hand-written datasets
# ---------------------------------------------------------------------------


def _dataset(quads: List[Tuple[Any, ...]]) -> Dataset:
    dataset = Dataset()
    for quad in quads:
        if len(quad) == 3:
            dataset.add(quad)
        else:
            subject, predicate, obj, graph_name = quad
            if graph_name is None:
                dataset.add((subject, predicate, obj))
            else:
                dataset.add((subject, predicate, obj, graph_name))
    return dataset


EX = "http://example.org/"
P = URIRef(EX + "p")


def test_cross_graph_shared_blank_node() -> None:
    """One blank node named in the default graph and used in a named graph."""
    shared = BNode("shared")
    dataset = Dataset()
    dataset.add((shared, URIRef(EX + "name"), Literal("shared")))
    dataset.add((URIRef(EX + "s"), URIRef(EX + "knows"), shared, URIRef(EX + "g")))

    output = canonicalize_dataset(dataset)

    assert output.count(b"\n") == 2
    label = b"_:c14n0"
    assert label + b" <" + EX.encode() + b"name> " in output
    assert b"<" + EX.encode() + b"s> <" + EX.encode() + b"knows> " + label in output


def test_blank_node_as_graph_name_and_subject() -> None:
    """A blank node can be a graph name and an ordinary node at once."""
    node = BNode("g")
    dataset = Dataset()
    dataset.add((node, URIRef(EX + "label"), Literal("the graph")))
    dataset.add((URIRef(EX + "s"), P, Literal("inside"), node))

    output = canonicalize_dataset(dataset)
    lines = output.decode("utf-8").splitlines()

    assert len(lines) == 2
    # Both mentions must carry the same canonical label.
    labels = set()
    for line in lines:
        for token in line.split():
            if token.startswith("_:"):
                labels.add(token)
    assert len(labels) == 1, lines


def test_self_loop() -> None:
    dataset = _dataset([(BNode("a"), P, BNode("a"), None)])
    assert (
        canonicalize_dataset(dataset) == b"_:c14n0 <" + EX.encode() + b"p> _:c14n0 .\n"
    )


def test_cycle_is_order_independent() -> None:
    def build(order):
        nodes = [BNode(name) for name in order]
        dataset = Dataset()
        for index in range(len(nodes)):
            dataset.add((nodes[index], P, nodes[(index + 1) % len(nodes)]))
        return canonicalize_dataset(dataset)

    first = build(["a", "b", "c"])
    second = build(["c", "b", "a"])
    assert first == second
    assert first.count(b"\n") == 3


def test_symmetric_disconnected_components() -> None:
    """Two isomorphic components are labelled deterministically."""

    def build(left, right):
        dataset = Dataset()
        for name in (left, right):
            node = BNode(name)
            dataset.add((node, P, Literal("leaf")))
        return canonicalize_dataset(dataset)

    assert build("x", "y") == build("y", "x")


def test_duplicate_quad_one_line_and_two_graphs_two_lines() -> None:
    triple = (URIRef(EX + "s"), P, Literal("o"))
    dataset = Dataset()
    dataset.add(triple)
    dataset.add(triple)  # duplicate in the default graph
    dataset.add(triple + (URIRef(EX + "g1"),))
    dataset.add(triple + (URIRef(EX + "g2"),))

    output = canonicalize_dataset(dataset)
    lines = output.decode("utf-8").splitlines()
    assert len(lines) == 3, lines
    assert sum(1 for line in lines if line.endswith(" <" + EX + "g1> .")) == 1
    assert sum(1 for line in lines if line.endswith(" <" + EX + "g2> .")) == 1


def test_default_graph_identifier_not_emitted() -> None:
    dataset = _dataset([(URIRef(EX + "s"), P, Literal("o"))])
    output = canonicalize_dataset(dataset)
    assert b"urn:x-rdflib:default" not in output
    assert output == b"<" + EX.encode() + b"s> <" + EX.encode() + b'p> "o" .\n'


def test_same_triple_in_default_and_named_graph_kept_twice() -> None:
    triple = (URIRef(EX + "s"), P, Literal("o"))
    dataset = Dataset()
    dataset.add(triple)  # default graph
    dataset.add(triple + (URIRef(EX + "g"),))
    output = canonicalize_dataset(dataset)
    assert output.count(b"\n") == 2
    assert output == (
        b"<" + EX.encode() + b"s> <" + EX.encode() + b'p> "o" .\n'
        b"<"
        + EX.encode()
        + b"s> <"
        + EX.encode()
        + b'p> "o" <'
        + EX.encode()
        + b"g> .\n"
    )


def test_shared_blank_node_in_default_and_named_graph() -> None:
    """One blank node used as a subject in the default graph and a graph name."""
    node = BNode("shared")
    dataset = Dataset()
    dataset.add((node, P, Literal("as subject")))
    dataset.add((URIRef(EX + "s"), P, Literal("as graph name"), node))

    output = canonicalize_dataset(dataset)
    lines = output.decode("utf-8").splitlines()
    # Exactly one canonical label for this node, used in both positions.
    labels = {
        token for line in lines for token in line.split() if token.startswith("_:")
    }
    assert labels == {"_:c14n0"}, lines
    assert any(line.endswith(" _:c14n0 .") for line in lines)
    assert any(line.startswith("_:c14n0 ") for line in lines)


def test_empty_dataset() -> None:
    assert canonicalize_dataset(Dataset()) == b""


def test_empty_named_graph_adds_no_output() -> None:
    dataset = Dataset()
    dataset.graph(URIRef(EX + "empty"))
    dataset.add((URIRef(EX + "s"), P, Literal("o")))
    output = canonicalize_dataset(dataset)
    assert output.count(b"\n") == 1
    assert b"empty" not in output


def test_escaping_non_ascii_language_and_datatype() -> None:
    dataset = _dataset(
        [
            (
                URIRef(EX + "s"),
                P,
                Literal('quote " back \\ tab \t nl \n cr \r ctrl \x00\x07\x7f'),
            ),
            (URIRef(EX + "s"), URIRef(EX + "label"), Literal("日本語 é", lang="en")),
            (
                URIRef(EX + "s"),
                URIRef(EX + "typed"),
                Literal("1", datatype=URIRef("urn:example:dt")),
            ),
            (URIRef(EX + "s"), URIRef(EX + "plain"), Literal("plain")),
        ]
    )
    output = canonicalize_dataset(dataset).decode("utf-8")
    assert (
        '"quote \\" back \\\\ tab \\t nl \\n cr \\r ctrl \\u0000\\u0007\\u007F"'
        in output
    )
    assert '"日本語 é"@en' in output
    assert '"1"^^<urn:example:dt>' in output
    assert '"plain"' in output


def test_xsd_string_datatype_is_dropped_but_not_normalized() -> None:
    dataset = _dataset(
        [
            (URIRef(EX + "s"), P, Literal("x")),
            (URIRef(EX + "s"), URIRef(EX + "q"), Literal("x", datatype=XSD.string)),
        ]
    )
    output = canonicalize_dataset(dataset).decode("utf-8")
    assert output.count('"x"') == 2
    assert "XMLSchema#string" not in output


def test_lexically_distinct_numeric_literals_are_kept_distinct() -> None:
    dataset = _dataset(
        [
            (
                URIRef(EX + "s"),
                P,
                Literal("01", datatype=XSD.integer, normalize=False),
            ),
            (
                URIRef(EX + "s"),
                P,
                Literal("1", datatype=XSD.integer, normalize=False),
            ),
        ]
    )
    output = canonicalize_dataset(dataset).decode("utf-8")
    assert '"01"^^<http://www.w3.org/2001/XMLSchema#integer>' in output
    assert '"1"^^<http://www.w3.org/2001/XMLSchema#integer>' in output
    assert len(output.splitlines()) == 2


def test_no_unicode_normalization_applied() -> None:
    # U+00E9 (é) and U+0065 U+0301 (e + combining acute) are distinct lexical
    # forms and must not be merged or normalized.
    dataset = _dataset(
        [
            (URIRef(EX + "s"), P, Literal("é")),
            (URIRef(EX + "s"), P, Literal("é")),
        ]
    )
    output = canonicalize_dataset(dataset).decode("utf-8")
    assert len(output.splitlines()) == 2
    assert "é" in output
    assert "é" in output


# ---------------------------------------------------------------------------
# Invariants: renaming, insertion order, prefixes, default_union, immutability
# ---------------------------------------------------------------------------


def _symmetric_dataset(names: List[str], default_union: bool = False) -> Dataset:
    """A dataset whose blank nodes are symmetric enough to need N-degree."""
    dataset = Dataset(default_union=default_union)
    nodes = [BNode(name) for name in names]
    for index, node in enumerate(nodes):
        dataset.add((node, P, nodes[(index + 1) % len(nodes)]))
        dataset.add((node, URIRef(EX + "q"), Literal("same")))
    return dataset


def test_blank_node_renaming_is_invisible() -> None:
    first = _symmetric_dataset(["a", "b", "c", "d"])
    second = _symmetric_dataset(["w", "x", "y", "z"])
    assert canonicalize_dataset(first) == canonicalize_dataset(second)


def test_insertion_order_is_invisible() -> None:
    quads = [
        (BNode("a"), P, BNode("b"), None),
        (BNode("b"), P, BNode("c"), URIRef(EX + "g")),
        (BNode("c"), P, BNode("a"), None),
        (BNode("a"), URIRef(EX + "q"), Literal("x"), URIRef(EX + "g")),
    ]
    forward = _dataset(quads)
    backward = _dataset(list(reversed(quads)))
    assert canonicalize_dataset(forward) == canonicalize_dataset(backward)


def test_prefix_bindings_are_invisible() -> None:
    dataset = _symmetric_dataset(["a", "b"])
    before = canonicalize_dataset(dataset)
    dataset.bind("ex", URIRef(EX))
    dataset.bind("other", URIRef("urn:example:"))
    assert canonicalize_dataset(dataset) == before


def test_default_union_does_not_change_the_result() -> None:
    quads = [
        (BNode("a"), P, BNode("b"), None),
        (BNode("b"), P, BNode("a"), URIRef(EX + "g")),
    ]
    off = _dataset(quads)
    on = _dataset(quads)
    on.default_union = True
    assert canonicalize_dataset(off) == canonicalize_dataset(on)


def test_input_is_not_modified() -> None:
    dataset = _symmetric_dataset(["a", "b"], default_union=True)
    dataset.bind("ex", URIRef(EX))
    quads_before = sorted(str(q) for q in dataset.quads((None, None, None, None)))
    graphs_before = sorted(str(g.identifier) for g in dataset.graphs())
    bindings_before = sorted(dataset.namespaces())

    canonicalize_dataset(dataset)

    assert (
        sorted(str(q) for q in dataset.quads((None, None, None, None))) == quads_before
    )
    assert sorted(str(g.identifier) for g in dataset.graphs()) == graphs_before
    assert sorted(dataset.namespaces()) == bindings_before
    assert dataset.default_union is True


def test_moving_a_triple_between_graphs_changes_output() -> None:
    triple = (URIRef(EX + "s"), P, Literal("o"))
    in_default = _dataset([triple + (None,)])
    in_named = _dataset([triple + (URIRef(EX + "g"),)])
    assert canonicalize_dataset(in_default) != canonicalize_dataset(in_named)


def test_changed_literal_lexical_form_changes_output() -> None:
    first = _dataset([(URIRef(EX + "s"), P, Literal("value"))])
    second = _dataset([(URIRef(EX + "s"), P, Literal("value "))])
    assert canonicalize_dataset(first) != canonicalize_dataset(second)


def test_repeated_and_interleaved_calls_do_not_share_state() -> None:
    a = _symmetric_dataset(["a", "b", "c"])
    b = _symmetric_dataset(["x", "y", "c"])
    first_a = canonicalize_dataset(a)
    first_b = canonicalize_dataset(b)
    assert canonicalize_dataset(a) == first_a
    assert canonicalize_dataset(b) == first_b
    assert canonicalize_dataset(a) == first_a


def test_recovery_after_error() -> None:
    bad = _dataset([(Literal("bad"), P, Literal("o"))])
    with pytest.raises(RDFCanonicalizationError):
        canonicalize_dataset(bad)
    good = _dataset([(URIRef(EX + "s"), P, Literal("o"))])
    assert (
        canonicalize_dataset(good)
        == b"<" + EX.encode() + b"s> <" + EX.encode() + b'p> "o" .\n'
    )


# ---------------------------------------------------------------------------
# Rejected input
# ---------------------------------------------------------------------------


def test_rejects_non_dataset() -> None:
    with pytest.raises(TypeError):
        canonicalize_dataset(Graph())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonicalize_dataset("not a dataset")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("subject", "predicate", "obj", "graph_name", "needle"),
    [
        (URIRef(EX + "s"), Literal("p"), Literal("o"), None, "predicate"),
        (URIRef(EX + "s"), BNode("p"), Literal("o"), None, "predicate"),
        (Literal("s"), URIRef(EX + "p"), Literal("o"), None, "subject"),
        (
            URIRef(EX + "s"),
            URIRef(EX + "p"),
            URIRef(EX + "o"),
            Literal("g"),
            "graph name",
        ),
        (URIRef("relative/path"), P, Literal("o"), None, "absolute IRI"),
        (URIRef(EX + "s"), URIRef("/relative/p"), Literal("o"), None, "absolute IRI"),
        (URIRef(EX + "s"), P, Literal("\ud800"), None, "surrogate"),
        (URIRef(EX + "s"), P, Literal("o"), URIRef(EX + "g\udfff"), "surrogate"),
    ],
)
def test_rejects_unrepresentable_quads(
    subject: Any,
    predicate: Any,
    obj: Any,
    graph_name: Optional[Any],
    needle: str,
) -> None:
    dataset = Dataset()
    if graph_name is None:
        dataset.add((subject, predicate, obj))
    else:
        dataset.add((subject, predicate, obj, graph_name))
    with pytest.raises(RDFCanonicalizationError) as catcher:
        canonicalize_dataset(dataset)
    message = str(catcher.value).lower()
    assert needle.lower() in message, message


def test_error_is_value_error() -> None:
    dataset = _dataset([(URIRef(EX + "s"), Literal("p"), Literal("o"))])
    with pytest.raises(ValueError):
        canonicalize_dataset(dataset)


# ---------------------------------------------------------------------------
# Dataset.serialize integration
# ---------------------------------------------------------------------------

SIMPLE_DATASET_OUTPUT = b"_:c14n0 <" + EX.encode() + b'p> "x" .\n'


def _simple_dataset() -> Dataset:
    return _dataset([(BNode("a"), P, Literal("x"))])


def test_serialize_returns_str_without_encoding() -> None:
    result = _simple_dataset().serialize(format="rdfc-1.0")
    assert isinstance(result, str)
    assert result == SIMPLE_DATASET_OUTPUT.decode("utf-8")


def test_serialize_returns_bytes_with_utf8_encoding() -> None:
    result = _simple_dataset().serialize(format="rdfc-1.0", encoding="utf-8")
    assert isinstance(result, bytes)
    assert result == SIMPLE_DATASET_OUTPUT


def test_serialize_matches_canonicalize_dataset() -> None:
    dataset = _symmetric_dataset(["a", "b", "c"])
    assert dataset.serialize(format="rdfc-1.0", encoding="utf-8") == (
        canonicalize_dataset(dataset)
    )


def test_serialize_to_binary_stream() -> None:
    dataset = _simple_dataset()
    stream = BytesIO()
    result = dataset.serialize(destination=stream, format="rdfc-1.0", encoding="utf-8")
    assert result is dataset
    assert stream.getvalue() == SIMPLE_DATASET_OUTPUT


def test_serialize_to_binary_stream_without_encoding() -> None:
    dataset = _simple_dataset()
    stream = BytesIO()
    dataset.serialize(destination=stream, format="rdfc-1.0")
    assert stream.getvalue() == SIMPLE_DATASET_OUTPUT


def test_serialize_to_path(tmp_path: pathlib.Path) -> None:
    dataset = _simple_dataset()
    target = tmp_path / "out.nq"
    result = dataset.serialize(destination=target, format="rdfc-1.0")
    assert result is dataset
    assert target.read_bytes() == SIMPLE_DATASET_OUTPUT


def test_serialize_to_str_path(tmp_path: pathlib.Path) -> None:
    dataset = _simple_dataset()
    target = tmp_path / "out.nq"
    dataset.serialize(destination=str(target), format="rdfc-1.0")
    assert target.read_bytes() == SIMPLE_DATASET_OUTPUT


def test_serialize_to_file_uri(tmp_path: pathlib.Path) -> None:
    dataset = _simple_dataset()
    target = tmp_path / "out.nq"
    dataset.serialize(destination=target.as_uri(), format="rdfc-1.0")
    assert target.read_bytes() == SIMPLE_DATASET_OUTPUT


def test_serialize_empty_dataset(tmp_path: pathlib.Path) -> None:
    empty = Dataset()
    assert empty.serialize(format="rdfc-1.0") == ""
    assert empty.serialize(format="rdfc-1.0", encoding="utf-8") == b""
    target = tmp_path / "empty.nq"
    empty.serialize(destination=target, format="rdfc-1.0")
    assert target.read_bytes() == b""


@pytest.mark.parametrize("format_name", ["rdfc-1.0", "application/rdfc-1.0"])
def test_serialize_accepts_both_plugin_names(format_name: str) -> None:
    assert _simple_dataset().serialize(format=format_name, encoding="utf-8") == (
        SIMPLE_DATASET_OUTPUT
    )


def test_serialize_rejects_non_utf8_encoding_before_writing() -> None:
    dataset = _simple_dataset()
    stream = BytesIO(b"prefix")
    with pytest.raises(ValueError):
        dataset.serialize(destination=stream, format="rdfc-1.0", encoding="latin-1")
    assert stream.getvalue() == b"prefix"


def test_serialize_rejects_base_before_writing() -> None:
    dataset = _simple_dataset()
    stream = BytesIO(b"prefix")
    with pytest.raises(ValueError):
        dataset.serialize(destination=stream, format="rdfc-1.0", base=EX)
    assert stream.getvalue() == b"prefix"


def test_serialize_rejects_base_when_returning() -> None:
    with pytest.raises(ValueError):
        _simple_dataset().serialize(format="rdfc-1.0", base=EX, encoding="utf-8")


def test_serialize_rejects_semantic_error_before_writing() -> None:
    dataset = _dataset([(URIRef(EX + "s"), Literal("p"), Literal("o"))])
    stream = BytesIO(b"prefix")
    with pytest.raises(ValueError):
        dataset.serialize(destination=stream, format="rdfc-1.0", encoding="utf-8")
    assert stream.getvalue() == b"prefix"


def test_serialize_leaves_existing_file_untouched_on_error() -> None:
    dataset = _dataset([(URIRef(EX + "s"), Literal("p"), Literal("o"))])
    stream = BytesIO(b"unchanged")
    with pytest.raises(ValueError):
        dataset.serialize(destination=stream, format="rdfc-1.0")
    assert stream.getvalue() == b"unchanged"


def test_serialize_path_failure_does_not_create_or_truncate(
    tmp_path: pathlib.Path,
) -> None:
    """A semantic error must be found before the destination path is opened."""
    missing = tmp_path / "not-created.nq"
    bad = _dataset([(URIRef(EX + "s"), Literal("p"), Literal("o"))])
    with pytest.raises(ValueError):
        bad.serialize(destination=missing, format="rdfc-1.0")
    assert not missing.exists()

    existing = tmp_path / "existing.nq"
    existing.write_bytes(b"original contents")
    with pytest.raises(ValueError):
        bad.serialize(destination=existing, format="rdfc-1.0")
    assert existing.read_bytes() == b"original contents"

    # A bad argument must likewise not disturb an existing path.
    with pytest.raises(ValueError):
        _simple_dataset().serialize(
            destination=existing, format="rdfc-1.0", encoding="latin-1"
        )
    assert existing.read_bytes() == b"original contents"

    # And a good call after the failures still works.
    _simple_dataset().serialize(destination=existing, format="rdfc-1.0")
    assert existing.read_bytes() == SIMPLE_DATASET_OUTPUT


def test_serialize_base_failure_does_not_create_path(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "not-created.nq"
    with pytest.raises(ValueError):
        _simple_dataset().serialize(destination=target, format="rdfc-1.0", base=EX)
    assert not target.exists()


def test_serialize_reuses_stream_after_failure() -> None:
    bad = _dataset([(URIRef(EX + "s"), Literal("p"), Literal("o"))])
    good = _simple_dataset()
    stream = BytesIO()
    with pytest.raises(ValueError):
        bad.serialize(destination=stream, format="rdfc-1.0")
    assert stream.getvalue() == b""
    good.serialize(destination=stream, format="rdfc-1.0")
    assert stream.getvalue() == SIMPLE_DATASET_OUTPUT


def test_serialize_is_repeatable() -> None:
    dataset = _simple_dataset()
    first = dataset.serialize(format="rdfc-1.0", encoding="utf-8")
    second = dataset.serialize(format="rdfc-1.0", encoding="utf-8")
    assert first == second


def test_plain_graph_rejects_rdfc_format() -> None:
    graph = Graph()
    graph.add((URIRef(EX + "s"), P, Literal("o")))
    with pytest.raises(TypeError):
        graph.serialize(format="rdfc-1.0")


def test_plain_graph_still_serializes_other_formats() -> None:
    graph = Graph()
    graph.add((URIRef(EX + "s"), P, Literal("o")))
    assert "<" + EX + "s>" in graph.serialize(format="nt")


def test_plugin_is_registered() -> None:
    import rdflib.plugin
    from rdflib.serializer import Serializer

    names = {plugin.name for plugin in rdflib.plugin.plugins(None, Serializer)}
    assert "rdfc-1.0" in names
    assert "application/rdfc-1.0" in names
