"""
This module demonstrates RDFC-1.0 canonicalization of a whole Dataset.

The W3C Recommendation *RDF Dataset Canonicalization* (RDFC-1.0, 2024-05-21)
defines a deterministic serialization for RDF datasets.  Two datasets that
are isomorphic -- same quads, same graph structure, same sharing of blank
nodes, but possibly different blank node labels or statement order --
serialize to exactly the same bytes.

RDFLib exposes it two ways:

* [`rdflib.compare.canonicalize_dataset`][], which returns UTF-8 ``bytes``;
* ``Dataset.serialize(format="rdfc-1.0")``, which follows the normal
  [`Graph.serialize`][rdflib.graph.Graph.serialize] contract (``str`` when no
  destination and no encoding is given, ``bytes`` when an encoding is given,
  writing to a path or binary stream otherwise).

The algorithm and the "canonical N-Quads" output format also cover blank
nodes used as graph names, blank nodes shared between graphs, and quads in
the default graph.  It is *not* a graph isomorphism check, a diff, or a
normalizer of literal values: lexical forms are preserved exactly as stored.

This example shows a dataset with

* one blank node shared between the default graph and an IRI-named graph;
* one blank node that is both a graph name and an ordinary subject;
* the same triple in the default graph and in a named graph (kept twice);
* an empty named graph (contributes nothing).
"""

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.compare import canonicalize_dataset

EX = "http://example.com/"

dataset = Dataset()
dataset.bind("ex", EX)

# A blank node that appears in the default graph and in a named graph.
shared = BNode("shared")
dataset.add((shared, URIRef(EX + "name"), Literal("shared node")))
dataset.add((URIRef(EX + "alice"), URIRef(EX + "knows"), shared, URIRef(EX + "g1")))

# A blank node that names a graph *and* appears as a subject inside it.
graph_node = BNode("graph-node")
dataset.add((graph_node, URIRef(EX + "label"), Literal("this graph")))
dataset.add((URIRef(EX + "bob"), URIRef(EX + "knows"), graph_node, graph_node))

# The same triple in the default graph and in a named graph: two output lines.
dataset.add((URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42")))
dataset.add(
    (URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42"), URIRef(EX + "g1"))
)

# An empty named graph: no output lines at all.
dataset.graph(URIRef(EX + "empty"))

canonical = canonicalize_dataset(dataset)
assert isinstance(canonical, bytes)

print(canonical.decode("utf-8"))
# <http://example.com/alice> <http://example.com/knows> _:c14n0 <http://example.com/g1> .
# <http://example.com/bob> <http://example.com/knows> _:c14n1 _:c14n1 .
# <http://example.com/carol> <http://example.com/age> "42" .
# <http://example.com/carol> <http://example.com/age> "42" <http://example.com/g1> .
# _:c14n0 <http://example.com/name> "shared node" .
# _:c14n1 <http://example.com/label> "this graph" .

# `_:c14n0` is the shared node: it appears as an object in the named graph and
# as a subject in the default graph.  `_:c14n1` is the graph node: it is both
# a graph name (last column) and a subject.  Neither `urn:x-rdflib:default`
# nor the empty graph appears anywhere.

# The serialize() route produces the same bytes and follows the usual rules.
assert dataset.serialize(format="rdfc-1.0", encoding="utf-8") == canonical
assert dataset.serialize(format="rdfc-1.0") == canonical.decode("utf-8")

# Re-labelling the blank nodes or reordering insertion cannot change the
# result.  (The dataset above is rebuilt with different labels.)
relabelled = Dataset()
relabelled.add(
    (URIRef(EX + "alice"), URIRef(EX + "knows"), BNode("x"), URIRef(EX + "g1"))
)
relabelled.add((BNode("x"), URIRef(EX + "name"), Literal("shared node")))
relabelled.add((BNode("y"), URIRef(EX + "label"), Literal("this graph")))
relabelled.add((URIRef(EX + "bob"), URIRef(EX + "knows"), BNode("y"), BNode("y")))
relabelled.add(
    (URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42"), URIRef(EX + "g1"))
)
relabelled.add((URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42")))
assert canonicalize_dataset(relabelled) == canonical

# Moving a triple from the default graph into a named graph changes the dataset,
# and therefore the canonical bytes.
moved = Dataset()
moved.add((URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42"), URIRef(EX + "g1")))
moved.add((URIRef(EX + "carol"), URIRef(EX + "age"), Literal("42"), URIRef(EX + "g1")))
assert canonicalize_dataset(moved) != canonical

# Lexical form matters: these are two different literals in RDF 1.1.
integers = Dataset()
integers.add(
    (
        URIRef(EX + "s"),
        URIRef(EX + "p"),
        Literal("1", datatype="http://www.w3.org/2001/XMLSchema#integer"),
    )
)
integers.add(
    (
        URIRef(EX + "s"),
        URIRef(EX + "p"),
        Literal(
            "01", datatype="http://www.w3.org/2001/XMLSchema#integer", normalize=False
        ),
    )
)
assert integers.serialize(format="rdfc-1.0").count("\n") == 2
