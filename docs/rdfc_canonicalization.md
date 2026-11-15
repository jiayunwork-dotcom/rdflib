# Canonicalizing datasets (RDFC-1.0)

RDFLib can serialize a whole [`Dataset`][rdflib.graph.Dataset] to the
[canonical N-Quads](#canonical-n-quads) form defined by the W3C Recommendation
**RDF Dataset Canonicalization** of **2024-05-21**, commonly called
**RDFC-1.0**.

The point of canonicalization is that the output depends only on the *RDF
dataset* — the set of quads, which graph each belongs to, and which blank
nodes are shared — and not on how the dataset was built.  Blank node labels,
statement insertion order, prefix bindings, and the `default_union` setting
all disappear from the result.  Two isomorphic datasets produce identical
bytes; two non-isomorphic datasets cannot.

It is useful for comparing or hashing datasets (for example, to deduplicate a
batch of RDF downloads), for storing a stable form, and for signing or
diffing.

```python
from rdflib import Dataset, URIRef, Literal
from rdflib.compare import canonicalize_dataset

dataset = Dataset()
dataset.add((URIRef("http://example.com/a"), URIRef("http://example.com/p"), Literal("x")))

canonicalize_dataset(dataset)      # b'<http://example.com/a> <http://example.com/p> "x" .\n'
dataset.serialize("rdfc-1.0")      # same document as a str
```

## API

Two entry points exist; both run the same implementation.

### `rdflib.compare.canonicalize_dataset`

```python
canonicalize_dataset(dataset: Dataset) -> bytes
```

Takes an [`rdflib.Dataset`][rdflib.graph.Dataset] and returns the canonical
N-Quads document as UTF-8 `bytes`.  An empty dataset returns `b""`.  Any other
object raises `TypeError`.

### `Dataset.serialize(format="rdfc-1.0")`

The usual [`Graph.serialize`][rdflib.graph.Graph.serialize] contract:

| destination | `encoding` | result |
| --- | --- | --- |
| not given | not given (`None`) | `str` |
| not given | `"utf-8"` | `bytes` |
| path / `file:` URI / `PurePath` | any (or `None`) | writes the file, returns the dataset |
| binary stream | any (or `None`) | writes the stream, returns the dataset |

`"application/rdfc-1.0"` is accepted as an alias.  Two restrictions:

* `encoding` must be `None` or UTF-8.  Canonical N-Quads is UTF-8 by
  definition, so any other encoding raises `ValueError`.
* `base` must be `None`.  Canonical N-Quads contains only absolute IRIs, so
  passing a base would either be meaningless or silently rewrite the data;
  it raises `ValueError` instead.

Both are checked, along with the dataset's own validity, *before* the
destination is opened or written.  A failed call never truncates an existing
file and never writes a partial document to a stream, so a stream you
pre-filled can still be reused afterwards.

The format is registered as a serializer plugin, but it only works for
`Dataset`.  Calling `Graph.serialize(format="rdfc-1.0")` raises `TypeError`,
because a plain graph is not a dataset.  Every other format on `Graph` is
unaffected.

## What is considered part of the dataset

RDFC-1.0 operates on an RDF dataset, which RDFLib models as:

* the **default graph**, stored under the internal identifier
  `urn:x-rdflib:default`;
* zero or more **named graphs**, whose names are IRIs or blank nodes;
* the **RDF 1.1 terms** stored in each graph.

Consequences that the implementation goes out of its way to honour:

* **The default graph and named graphs are one dataset.**  A triple in the
  default graph and the same triple in a named graph are two quads and produce
  two lines.
* **The default graph identifier is internal.**  `urn:x-rdflib:default` never
  appears in the output and is not treated as graph content.
* **A blank node may be shared across graphs**, and may simultaneously be a
  graph name and a subject or object.  It keeps a single identity, so it gets
  a single canonical label.
* **Empty named graphs contribute nothing.**  A named graph with no triples has
  no quads, so it adds no output lines.  
* **Duplicates collapse per graph.**  Adding the same quad twice yields one
  line; the same triple in two graphs yields two.
* **Graphs are never merged.**  Named graphs stay distinct even when their
  triples are equal.

## Terms are used as stored

RDFC-1.0 canonicalizes the RDF dataset; it does not normalize it.  This
implementation therefore passes terms through unchanged:

* **No literal value normalization.**  `"01"^^xsd:integer` and
  `"1"^^xsd:integer` are distinct terms and stay distinct.  If the dataset was
  built with `normalize=False`, the different lexical forms survive to the
  output.
* **No Unicode normalization.**  `é` and `e` + combining acute are different
  lexical forms and stay different.
* **No IRI resolution.**  Relative IRIs are *rejected* (see below) rather than
  resolved against a base.
* **Escaping follows canonical N-Quads** (see the next section), so
  non-ASCII, escapes, language tags and datatypes are written in exactly one
  way.

The `xsd:string` datatype is the one syntactic exception: canonical N-Quads
requires a simple literal (`"x"`) and RDF 1.1 defines that to be the same term
as `"x"^^xsd:string`, so the explicit datatype is dropped.  This is part of
the format, not a normalization of the value.

## Canonical N-Quads

The output is an N-Quads document with one quad per line, a single space
between components, no comments, and a final LF.  It is sorted by Unicode code
point (equivalent to sorted UTF-8 bytes).  The exact escaping rules (Appendix A
of the Recommendation) are:

* In literals, `"` `\` `\n` `\r` `\t` `\b` `\f` use the short `ECHAR` escapes;
  other C0 controls and DEL use `\uXXXX` with **uppercase** hex; all other
  characters are written literally (so non-ASCII text, including emoji, is
  UTF-8, not `\u`-escaped).
* In IRIs, characters up to and including U+0020 and the set
  `` <>"{}|^`\ `` are written as `\uXXXX`/`\UXXXXXXXX`; everything else is
  written literally.
* Literals with a language tag are `"lexical"@tag`; other typed literals are
  `"lexical"^^<datatype>`; untyped and `xsd:string` literals are `"lexical"`.
* Blank nodes are labelled `_:c14n0`, `_:c14n1`, … in canonical order.
* A graph name, if present, follows the object.  The default graph has none.

## Rejected input

`ValueError` (specifically
[`RDFCanonicalizationError`][rdflib.compare.RDFCanonicalizationError], a
subclass) is raised when a quad has no RDF 1.1 interpretation in canonical
N-Quads form:

* a predicate that is a literal or blank node (only IRIs may be predicates);
* a subject or object that is a literal, or an object that is not an IRI,
  blank node or literal;
* a graph name that is a literal;
* an IRI that is relative (no scheme), since canonical N-Quads has no base;
* an unpaired surrogate anywhere in an IRI or literal;
* an `rdf:langString` literal with no language tag.

The message names the offending term and the position in the quad
(`subject`, `predicate`, `object`, `graph name`, `datatype`).  RDFLib's store
does not promise a discovery order, so when several quads are invalid the one
reported is unspecified; the call always fails before producing output.

A different `RDFCanonicalizationError` is raised for input that is valid RDF
but too expensive to canonicalize; see the next section.

## Cost and highly symmetric input

RDFC-1.0 is fast when blank nodes can be told apart cheaply: every blank node
is hashed together with the quads that mention it ("first degree"), and a
unique hash immediately fixes its label.  Datasets with no blank nodes, or
with well-differentiated ones, run in roughly linear time in the number of
quads.

Trouble comes from blank nodes that look alike.  When several share a
first-degree hash, the algorithm explores the paths connecting them in
section 4.8, and in the worst case tries permutations of groups of related
nodes.  Section 7.1 of the Recommendation calls these *poison* datasets and
requires implementations to refuse them rather than run indefinitely.

This implementation caps the number of N-degree hash invocations, separately
for each group of blank nodes that share a first-degree hash, at
`max(1024, 64 × (size of that group)²)`.  The per-group reset matters: a small
symmetric component inside a large, otherwise well-behaved dataset is judged
on its own size, so the budget for it is not stretched by the rest of the data.
Measured on a Linux container (CPython 3.11):

| input | quads | time | output |
| --- | --- | --- | --- |
| 50,000 quads, no blank nodes | 50,000 | ~1.3 s | 5.2 MB |
| 2,000 asymmetric blank nodes (uneven chain) | 4,664 | ~0.06 s | 235 KB |
| W3C poison vector (10-node clique) | 100 | ~0.4 s, then `RDFCanonicalizationError` | — |
| cycle of 100 identical blank nodes | 100 | ~0.07 s | — |
| cycle of 1,000 identical blank nodes | 1,000 | ~18 s | — |

Peak memory for the 50,000-quad run was about 180 MiB for the whole process
(including RDFLib import and parsing), not per-quad.  The reproduction script
is `test/test_graph/benchmark_rdfc.py`.

The practical limits follow from this:

* Ordinary RDF (IRIs, literals, a few blank nodes) is always fine.
* Deeply *symmetric* structures — a large clique of blank nodes, or a long
  cycle with no distinguishing labels — are where RDFC-1.0 is inherently
  quadratic or worse.  They either take seconds to minutes or are refused.
* Refusal is deterministic and is not an incorrect answer: there is no
  partially canonical output.

Symmetry does **not** mean "many blank nodes".  A dataset with 2,000 blank
nodes that each have a distinct literal label canonicalizes in milliseconds;
a ten-node clique is refused.  Nodes only compete with each other when they
land in the same first-degree-hash group.  If you control the data, adding a
distinguishing property to otherwise indistinguishable nodes removes the
problem.  One consequence worth knowing: when two hub nodes have identical
labels and are connected to groups of otherwise unlabelled "leaf" nodes, the
permutation search can be factorial in the number of leaves, and sufficiently
many such leaves will trip the budget.

## Guarantees and non-guarantees

The call:

* does **not** modify the dataset — quads, node identities, the graph list,
  prefix bindings and `default_union` are all left as they were;
* does **not** retain state between calls, so repeated calls, interleaved calls
  on different datasets, and calls after a failure all behave independently;
* assumes the dataset is not modified concurrently while it runs.

What it does not do: it is not a diff, not an isomorphism *test* (it produces a
canonical form rather than a boolean, though comparing two outputs achieves
that), and not a merger.  It also does not expose the issued-identifier map
(the `_:c14nN` label for each original blank node); only the document is
returned.

## Conformance

The implementation is checked byte-for-byte against the W3C
[rdf-canon test suite](https://github.com/w3c/rdf-canon), pinned at commit
`5ffaaa13c0e19a2a2f9e60d0ecd9efaf3edab633` and vendored under
`test/data/suites/w3c/rdf-canon/`.

All 63 applicable RDFC-1.0 evaluation vectors (SHA-256) pass, byte for byte,
plus the negative poison vector and 20 of the 21 issued-identifier-map tests.
Excluded, with reasons:

* `#test075c` and `#test075m` — parameterized for SHA-384; this API exposes
  SHA-256 only.
* URGNA2012 vectors — a different (older) algorithm, not part of RDFC-1.0.

The map tests inspect the internal blank-node label assignment, which is not
part of the public API; they are tested via the private canonicalizer in
`test/test_graph/test_rdfc_identifier_map.py`, so a future refactor of the
internals may require updating those tests.
