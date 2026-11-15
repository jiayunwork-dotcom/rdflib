"""
A collection of utilities for canonicalizing and inspecting graphs.

Among other things, they solve of the problem of deterministic bnode
comparisons.

Warning: the time to canonicalize bnodes may increase exponentially on
degenerate larger graphs. Use with care!

Example of comparing two graphs:

```python
>>> g1 = Graph().parse(format='n3', data='''
...     @prefix : <http://example.org/ns#> .
...     <http://example.org> :rel
...         <http://example.org/same>,
...         [ :label "Same" ],
...         <http://example.org/a>,
...         [ :label "A" ] .
... ''')
>>> g2 = Graph().parse(format='n3', data='''
...     @prefix : <http://example.org/ns#> .
...     <http://example.org> :rel
...         <http://example.org/same>,
...         [ :label "Same" ],
...         <http://example.org/b>,
...         [ :label "B" ] .
... ''')
>>>
>>> iso1 = to_isomorphic(g1)
>>> iso2 = to_isomorphic(g2)

```

These are not isomorphic

```python
>>> iso1 == iso2
False

```

Diff the two graphs:

```python
>>> in_both, in_first, in_second = graph_diff(iso1, iso2)

```

Present in both:

```python
>>> def dump_nt_sorted(g):
...     for l in sorted(g.serialize(format='nt').splitlines()):
...         if l: print(l.decode('ascii'))
>>> dump_nt_sorted(in_both) #doctest: +SKIP
<http://example.org>
    <http://example.org/ns#rel> <http://example.org/same> .
<http://example.org>
    <http://example.org/ns#rel> _:cbcaabaaba17fecbc304a64f8edee4335e .
_:cbcaabaaba17fecbc304a64f8edee4335e
    <http://example.org/ns#label> "Same" .
```

Only in first:

```python
>>> dump_nt_sorted(in_first) #doctest: +SKIP
<http://example.org>
    <http://example.org/ns#rel> <http://example.org/a> .
<http://example.org>
    <http://example.org/ns#rel> _:cb124e4c6da0579f810c0ffe4eff485bd9 .
_:cb124e4c6da0579f810c0ffe4eff485bd9
    <http://example.org/ns#label> "A" .
```

Only in second:

```python
>>> dump_nt_sorted(in_second) #doctest: +SKIP
<http://example.org>
    <http://example.org/ns#rel> <http://example.org/b> .
<http://example.org>
    <http://example.org/ns#rel> _:cb558f30e21ddfc05ca53108348338ade8 .
_:cb558f30e21ddfc05ca53108348338ade8
    <http://example.org/ns#label> "B" .
```
"""

from __future__ import annotations

# TODO:
# - Doesn't handle quads.
# - Add warning and/or safety mechanism before working on large graphs?
# - use this in existing Graph.isomorphic?

__all__ = [
    "IsomorphicGraph",
    "to_isomorphic",
    "isomorphic",
    "to_canonical_graph",
    "graph_diff",
    "similar",
    "canonicalize_dataset",
    "RDFCanonicalizationError",
]

import itertools
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from rdflib.graph import (
    DATASET_DEFAULT_GRAPH_ID,
    ConjunctiveGraph,
    Dataset,
    Graph,
    ReadOnlyGraphAggregate,
    _TripleType,
)
from rdflib.term import BNode, IdentifiedNode, Literal, Node, URIRef

if TYPE_CHECKING:
    from _hashlib import HASH


def _total_seconds(td):
    result = td.days * 24 * 60 * 60
    result += td.seconds
    result += td.microseconds / 1000000.0
    return result


class _runtime:  # noqa: N801
    def __init__(self, label):
        self.label = label

    def __call__(self, f):
        if self.label is None:
            self.label = f.__name__ + "_runtime"

        def wrapped_f(*args, **kwargs):
            start = datetime.now()
            result = f(*args, **kwargs)
            if "stats" in kwargs and kwargs["stats"] is not None:
                stats = kwargs["stats"]
                stats[self.label] = _total_seconds(datetime.now() - start)
            return result

        return wrapped_f


class _call_count:  # noqa: N801
    def __init__(self, label):
        self.label = label

    def __call__(self, f):
        if self.label is None:
            self.label = f.__name__ + "_runtime"

        def wrapped_f(*args, **kwargs):
            if "stats" in kwargs and kwargs["stats"] is not None:
                stats = kwargs["stats"]
                if self.label not in stats:
                    stats[self.label] = 0
                stats[self.label] += 1
            return f(*args, **kwargs)

        return wrapped_f


class IsomorphicGraph(ConjunctiveGraph):
    """An implementation of the RGDA1 graph digest algorithm.

    An implementation of RGDA1 (publication below),
    a combination of Sayers & Karp's graph digest algorithm using
    sum and SHA-256 <http://www.hpl.hp.com/techreports/2003/HPL-2003-235R1.pdf>
    and traces <http://pallini.di.uniroma1.it>, an average case
    polynomial time algorithm for graph canonicalization.

    McCusker, J. P. (2015). WebSig: A Digital Signature Framework for the Web.
    Rensselaer Polytechnic Institute, Troy, NY.
    http://gradworks.umi.com/3727015.pdf
    """

    def __init__(self, **kwargs):
        super(IsomorphicGraph, self).__init__(**kwargs)

    def __eq__(self, other):
        """Graph isomorphism testing."""
        if not isinstance(other, IsomorphicGraph):
            return False
        elif len(self) != len(other):
            return False
        return self.internal_hash() == other.internal_hash()

    def __ne__(self, other):
        """Negative graph isomorphism testing."""
        return not self.__eq__(other)

    def __hash__(self):
        return super(IsomorphicGraph, self).__hash__()

    def graph_digest(self, stats=None):
        """Synonym for IsomorphicGraph.internal_hash."""
        return self.internal_hash(stats=stats)

    def internal_hash(self, stats=None):
        """
        This is defined instead of `__hash__` to avoid a circular recursion
        scenario with the Memory store for rdflib which requires a hash lookup
        in order to return a generator of triples.
        """
        return _TripleCanonicalizer(self).to_hash(stats=stats)


HashFunc = Callable[[str], int]
ColorItem = Tuple[Union[int, str], URIRef, Union[int, str]]
ColorItemTuple = Tuple[ColorItem, ...]
HashCache = Optional[Dict[ColorItemTuple, str]]
Stats = Dict[str, Union[int, str]]


class Color:
    def __init__(
        self,
        nodes: List[IdentifiedNode],
        hashfunc: HashFunc,
        color: ColorItemTuple = (),
        hash_cache: HashCache = None,
    ):
        if hash_cache is None:
            hash_cache = {}
        self._hash_cache = hash_cache
        self.color = color
        self.nodes = nodes
        self.hashfunc = hashfunc
        self._hash_color = None

    def __str__(self):
        nodes, color = self.key()
        return "Color %s (%s nodes)" % (color, nodes)

    def key(self):
        return (len(self.nodes), self.hash_color())

    def hash_color(self, color: Optional[Tuple[ColorItem, ...]] = None) -> str:
        if color is None:
            color = self.color
        if color in self._hash_cache:
            return self._hash_cache[color]

        def stringify(x):
            if isinstance(x, Node):
                return x.n3()
            else:
                return str(x)

        if isinstance(color, Node):
            return stringify(color)
        value = 0
        for triple in color:
            value += self.hashfunc(" ".join([stringify(x) for x in triple]))
        val: str = "%x" % value
        self._hash_cache[color] = val
        return val

    def distinguish(self, W: Color, graph: Graph):  # noqa: N803
        colors: Dict[str, Color] = {}
        for n in self.nodes:
            new_color: Tuple[ColorItem, ...] = list(self.color)  # type: ignore[assignment]
            for node in W.nodes:
                new_color += [  # type: ignore[operator]
                    (1, p, W.hash_color()) for s, p, o in graph.triples((n, None, node))
                ]
                new_color += [  # type: ignore[operator]
                    (W.hash_color(), p, 3) for s, p, o in graph.triples((node, None, n))
                ]
            new_color = tuple(new_color)
            new_hash_color = self.hash_color(new_color)

            if new_hash_color not in colors:
                c = Color([], self.hashfunc, new_color, hash_cache=self._hash_cache)
                colors[new_hash_color] = c
            colors[new_hash_color].nodes.append(n)
        return colors.values()

    def discrete(self):
        return len(self.nodes) == 1

    def copy(self):
        return Color(
            self.nodes[:], self.hashfunc, self.color, hash_cache=self._hash_cache
        )


_HashT = Callable[[], "HASH"]


class _TripleCanonicalizer:
    def __init__(self, graph: Graph, hashfunc: _HashT = sha256):
        self.graph = graph

        def _hashfunc(s: str):
            h = hashfunc()
            h.update(str(s).encode("utf8"))
            return int(h.hexdigest(), 16)

        self._hash_cache: HashCache = {}
        self.hashfunc = _hashfunc

    def _discrete(self, coloring: List[Color]) -> bool:
        return len([c for c in coloring if not c.discrete()]) == 0

    def _initial_color(self) -> List[Color]:
        """Finds an initial color for the graph.

        Finds an initial color of the graph by finding all blank nodes and
        non-blank nodes that are adjacent. Nodes that are not adjacent to blank
        nodes are not included, as they are a) already colored (by URI or literal)
        and b) do not factor into the color of any blank node.
        """
        bnodes: Set[BNode] = set()
        others = set()
        self._neighbors = defaultdict(set)
        for s, p, o in self.graph:
            nodes = set([s, p, o])
            b = set([x for x in nodes if isinstance(x, BNode)])
            if len(b) > 0:
                others |= nodes - b
                bnodes |= b
                if isinstance(s, BNode):
                    self._neighbors[s].add(o)
                if isinstance(o, BNode):
                    self._neighbors[o].add(s)
                if isinstance(p, BNode):
                    self._neighbors[p].add(s)
                    self._neighbors[p].add(p)
        if len(bnodes) > 0:
            return [Color(list(bnodes), self.hashfunc, hash_cache=self._hash_cache)] + [
                # type error: List item 0 has incompatible type "Union[IdentifiedNode, Literal]"; expected "IdentifiedNode"
                # type error: Argument 3 to "Color" has incompatible type "Union[IdentifiedNode, Literal]"; expected "Tuple[Tuple[Union[int, str], URIRef, Union[int, str]], ...]"
                Color([x], self.hashfunc, x, hash_cache=self._hash_cache)  # type: ignore[list-item, arg-type]
                for x in others
            ]
        else:
            return []

    def _individuate(self, color, individual):
        new_color = list(color.color)
        new_color.append((len(color.nodes),))

        color.nodes.remove(individual)
        c = Color(
            [individual], self.hashfunc, tuple(new_color), hash_cache=self._hash_cache
        )
        return c

    def _get_candidates(self, coloring: List[Color]) -> Iterator[Tuple[Node, Color]]:
        for c in [c for c in coloring if not c.discrete()]:
            for node in c.nodes:
                yield node, c

    def _refine(self, coloring: List[Color], sequence: List[Color]) -> List[Color]:
        sequence = sorted(sequence, key=lambda x: x.key(), reverse=True)
        coloring = coloring[:]
        while len(sequence) > 0 and not self._discrete(coloring):
            W = sequence.pop()  # noqa: N806
            for c in coloring[:]:
                if len(c.nodes) > 1 or isinstance(c.nodes[0], BNode):
                    colors = sorted(
                        c.distinguish(W, self.graph),
                        key=lambda x: x.key(),
                        reverse=True,
                    )
                    coloring.remove(c)
                    coloring.extend(colors)
                    try:
                        si = sequence.index(c)
                        sequence = sequence[:si] + colors + sequence[si + 1 :]
                    except ValueError:
                        sequence = colors[1:] + sequence
        combined_colors: List[Color] = []
        combined_color_map: Dict[str, Color] = dict()
        for color in coloring:
            color_hash = color.hash_color()
            # This is a hash collision, and be combined into a single color for individuation.
            if color_hash in combined_color_map:
                combined_color_map[color_hash].nodes.extend(color.nodes)
            else:
                combined_colors.append(color)
                combined_color_map[color_hash] = color
        return combined_colors

    @_runtime("to_hash_runtime")
    def to_hash(self, stats: Optional[Stats] = None):
        result = 0
        for triple in self.canonical_triples(stats=stats):
            result += self.hashfunc(" ".join([x.n3() for x in triple]))
        if stats is not None:
            stats["graph_digest"] = "%x" % result
        return result

    def _experimental_path(self, coloring: List[Color]) -> List[Color]:
        coloring = [c.copy() for c in coloring]
        while not self._discrete(coloring):
            color = [x for x in coloring if not x.discrete()][0]
            node = color.nodes[0]
            new_color = self._individuate(color, node)
            coloring.append(new_color)
            coloring = self._refine(coloring, [new_color])
        return coloring

    def _create_generator(
        self,
        colorings: List[List[Color]],
        groupings: Optional[Dict[Node, Set[Node]]] = None,
    ) -> Dict[Node, Set[Node]]:
        if not groupings:
            groupings = defaultdict(set)
        for group in zip(*colorings):
            g = set([c.nodes[0] for c in group])
            for n in group:
                g |= groupings[n]
            for n in g:
                groupings[n] = g
        return groupings

    @_call_count("individuations")
    def _traces(
        self,
        coloring: List[Color],
        stats: Optional[Stats] = None,
        depth: List[int] = [0],
    ) -> List[Color]:
        if stats is not None and "prunings" not in stats:
            stats["prunings"] = 0
        depth[0] += 1
        candidates = self._get_candidates(coloring)
        best: List[List[Color]] = []
        best_score = None
        best_experimental_score = None
        last_coloring = None
        generator: Dict[Node, Set[Node]] = defaultdict(set)
        visited: Set[Node] = set()
        for candidate, color in candidates:
            if candidate in generator:
                v = generator[candidate] & visited
                if len(v) > 0:
                    visited.add(candidate)
                    continue
            visited.add(candidate)
            coloring_copy: List[Color] = []
            color_copy = None
            for c in coloring:
                c_copy = c.copy()
                coloring_copy.append(c_copy)
                if c == color:
                    color_copy = c_copy
            new_color = self._individuate(color_copy, candidate)
            coloring_copy.append(new_color)
            refined_coloring = self._refine(coloring_copy, [new_color])
            color_score = tuple([c.key() for c in refined_coloring])
            experimental = self._experimental_path(coloring_copy)
            experimental_score = set([c.key() for c in experimental])
            if last_coloring:
                # type error: Statement is unreachable
                generator = self._create_generator(  # type: ignore[unreachable]
                    [last_coloring, experimental], generator
                )
            last_coloring = experimental
            if best_score is None or best_score < color_score:  # type: ignore[unreachable]
                best = [refined_coloring]
                best_score = color_score
                best_experimental_score = experimental_score
            elif best_score > color_score:  # type: ignore[unreachable]
                # prune this branch.
                if stats is not None and isinstance(stats["prunings"], int):
                    stats["prunings"] += 1
            elif experimental_score != best_experimental_score:
                best.append(refined_coloring)
            else:
                # prune this branch.
                if stats is not None and isinstance(stats["prunings"], int):
                    stats["prunings"] += 1
        discrete: List[List[Color]] = [x for x in best if self._discrete(x)]
        if len(discrete) == 0:
            best_score = None
            best_depth = None
            for coloring in best:
                d = [depth[0]]
                new_color = self._traces(coloring, stats=stats, depth=d)
                color_score = tuple([c.key() for c in refined_coloring])
                if best_score is None or color_score > best_score:  # type: ignore[unreachable]
                    discrete = [new_color]
                    best_score = color_score
                    best_depth = d[0]
            depth[0] = best_depth  # type: ignore[assignment]
        return discrete[0]

    def canonical_triples(self, stats: Optional[Stats] = None):
        if stats is not None:
            start_coloring = datetime.now()
        coloring = self._initial_color()
        if stats is not None:
            stats["triple_count"] = len(self.graph)
            stats["adjacent_nodes"] = max(0, len(coloring) - 1)
        coloring = self._refine(coloring, coloring[:])
        if stats is not None:
            stats["initial_coloring_runtime"] = _total_seconds(
                datetime.now() - start_coloring
            )
            stats["initial_color_count"] = len(coloring)

        if not self._discrete(coloring):
            depth = [0]
            coloring = self._traces(coloring, stats=stats, depth=depth)
            if stats is not None:
                stats["tree_depth"] = depth[0]
        elif stats is not None:
            stats["individuations"] = 0
            stats["tree_depth"] = 0
        if stats is not None:
            stats["color_count"] = len(coloring)

        bnode_labels: Dict[Node, str] = dict(
            [(c.nodes[0], c.hash_color()) for c in coloring]
        )
        if stats is not None:
            stats["canonicalize_triples_runtime"] = _total_seconds(
                datetime.now() - start_coloring
            )
        for triple in self.graph:
            result = tuple(self._canonicalize_bnodes(triple, bnode_labels))
            yield result

    def _canonicalize_bnodes(
        self,
        triple: _TripleType,
        labels: Dict[Node, str],
    ):
        for term in triple:
            if isinstance(term, BNode):
                yield BNode(value="cb%s" % labels[term])
            else:
                yield term


def to_isomorphic(graph: Graph) -> IsomorphicGraph:
    if isinstance(graph, IsomorphicGraph):
        return graph
    result = IsomorphicGraph()
    if hasattr(graph, "identifier"):
        result = IsomorphicGraph(identifier=graph.identifier)
    result += graph
    return result


def isomorphic(graph1: Graph, graph2: Graph) -> bool:
    """Compare graph for equality.

    Uses an algorithm to compute unique hashes which takes bnodes into account.

    Example:
        ```python
        >>> g1 = Graph().parse(format='n3', data='''
        ...     @prefix : <http://example.org/ns#> .
        ...     <http://example.org> :rel <http://example.org/a> .
        ...     <http://example.org> :rel <http://example.org/b> .
        ...     <http://example.org> :rel [ :label "A bnode." ] .
        ... ''')
        >>> g2 = Graph().parse(format='n3', data='''
        ...     @prefix ns: <http://example.org/ns#> .
        ...     <http://example.org> ns:rel [ ns:label "A bnode." ] .
        ...     <http://example.org> ns:rel <http://example.org/b>,
        ...             <http://example.org/a> .
        ... ''')
        >>> isomorphic(g1, g2)
        True
        >>> g3 = Graph().parse(format='n3', data='''
        ...     @prefix : <http://example.org/ns#> .
        ...     <http://example.org> :rel <http://example.org/a> .
        ...     <http://example.org> :rel <http://example.org/b> .
        ...     <http://example.org> :rel <http://example.org/c> .
        ... ''')
        >>> isomorphic(g1, g3)
        False

        ```
    """
    gd1 = _TripleCanonicalizer(graph1).to_hash()
    gd2 = _TripleCanonicalizer(graph2).to_hash()
    return gd1 == gd2


def to_canonical_graph(
    g1: Graph, stats: Optional[Stats] = None
) -> ReadOnlyGraphAggregate:
    """Creates a canonical, read-only graph.

    Creates a canonical, read-only graph where all bnode id:s are based on
    deterministical SHA-256 checksums, correlated with the graph contents.
    """
    graph = Graph()
    graph += _TripleCanonicalizer(g1).canonical_triples(stats=stats)
    return ReadOnlyGraphAggregate([graph])


def graph_diff(g1: Graph, g2: Graph) -> Tuple[Graph, Graph, Graph]:
    """Returns three sets of triples: "in both", "in first" and "in second"."""
    # bnodes have deterministic values in canonical graphs:
    cg1 = to_canonical_graph(g1)
    cg2 = to_canonical_graph(g2)
    in_both = cg1 * cg2
    in_first = cg1 - cg2
    in_second = cg2 - cg1
    return (in_both, in_first, in_second)


_MOCK_BNODE = BNode()


def similar(g1: Graph, g2: Graph):
    """Checks if the two graphs are "similar".

    Checks if the two graphs are "similar", by comparing sorted triples where
    all bnodes have been replaced by a singular mock bnode (the
    `_MOCK_BNODE`).

    This is a much cheaper, but less reliable, alternative to the comparison
    algorithm in `isomorphic`.
    """
    return all(t1 == t2 for (t1, t2) in _squashed_graphs_triples(g1, g2))


def _squashed_graphs_triples(g1: Graph, g2: Graph):
    for t1, t2 in zip(sorted(_squash_graph(g1)), sorted(_squash_graph(g2))):
        yield t1, t2


def _squash_graph(graph: Graph):
    return (_squash_bnodes(triple) for triple in graph)


def _squash_bnodes(triple):
    return tuple((isinstance(t, BNode) and _MOCK_BNODE) or t for t in triple)


# ---------------------------------------------------------------------------
# RDF Dataset Canonicalization (RDFC-1.0)
# ---------------------------------------------------------------------------
#
# An independent implementation of the RDFC-1.0 algorithm defined by the W3C
# Recommendation "RDF Dataset Canonicalization" of 2024-05-21
# (https://www.w3.org/TR/2024/REC-rdf-canon-20240521/), together with the
# canonical N-Quads form defined in Appendix A of that document.
#
# The implementation operates on the RDF 1.1 terms RDFLib has already stored.
# It does not normalize literals, resolve relative IRIs or merge values; the
# only rewriting is the replacement of blank node labels by deterministic
# `_:c14nN` labels.  Three positions in a quad are blank-node-capable: the
# subject, the object and the graph name.  A blank node may be shared by any
# number of passages, so a node that is both a graph name and a subject is
# treated as one node.

_RDF_LANG_STRING = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
_XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"

#: Prefix for canonical blank node labels (RDFC-1.0 section 4.2).
_RDFC_CANONICAL_PREFIX = "c14n"
#: Prefix for temporary identifiers (RDFC-1.0 section 4.8).
_RDFC_TEMPORARY_PREFIX = "b"

#: Budget for Hash N-Degree Quads invocations, per symmetry group.
#:
#: RDFC-1.0 is a partial canonicalization function: section 7.1 describes
#: "poison" datasets whose gossip paths make step 6 explore a number of
#: permutation branches that grows exponentially with the number of blank
#: nodes.  Implementations are required to detect these and fail rather than
#: run indefinitely, so each group of blank nodes sharing a first-degree hash
#: gets a budget on N-degree invocations, quadratic in the group size:
#:
#: * ordinary datasets need no N-degree calls at all (unique first-degree
#:   hashes), and the hard W3C vectors (test044..046, 12 blank nodes in one
#:   group) need 468;
#: * a cycle of `n` identical blank nodes needs `n^2` calls, so the budget
#:   accommodates symmetric structures in the thousands;
#: * the W3C poison vector (a 10-node clique) blows through it almost
#:   immediately, which is the behaviour the negative vector asks for.
_RDFC_NDEGREE_BUDGET_FACTOR = 64
_RDFC_NDEGREE_BUDGET_FLOOR = 1024


def _rdfc_ndegree_budget(group_size: int) -> int:
    """N-degree invocation budget for one first-degree-hash group."""
    return max(
        _RDFC_NDEGREE_BUDGET_FLOOR,
        _RDFC_NDEGREE_BUDGET_FACTOR * group_size * group_size,
    )


class RDFCanonicalizationError(ValueError):
    """A dataset cannot be expressed in canonical N-Quads form.

    Subclasses [`ValueError`][], so callers that treat "unusable input" as a
    single case can catch that.  Two situations raise it:

    * the dataset contains something RDF 1.1 does not allow, such as a
      literal predicate, a relative IRI, or an unpaired surrogate; the
      message names the offending term and its position in the quad;
    * the dataset is so highly symmetric that RDFC-1.0 would need an
      unreasonable number of N-degree hashes ("dataset poisoning", see
      section 7.1 of the Recommendation).
    """


def _rdfc_term_kind(term: Node) -> str:
    if isinstance(term, URIRef):
        return "IRI"
    if isinstance(term, BNode):
        return "blank node"
    if isinstance(term, Literal):
        return "literal"
    return type(term).__name__


def _rdfc_check_absolute_iri(value: str, position: str) -> None:
    """Reject IRIs that are not absolute, as RDF 1.1 requires.

    RDFLib accepts relative strings in [`URIRef`][rdflib.term.URIRef] (it only
    warns), so the check lives here rather than relying on construction.  RFC
    3987 / RFC 3986 absolute IRIs start with a scheme; ``urlsplit`` recognizes
    the scheme without validating the remainder, which is enough to
    distinguish ``urn:x`` from ``/relative`` or ``relative/path``.
    """
    from urllib.parse import urlsplit

    if urlsplit(value).scheme:
        return
    raise RDFCanonicalizationError(
        f"cannot canonicalize quad: {position} is not an absolute IRI ({value!r})"
    )


def _rdfc_escape_iri(value: str, position: str) -> str:
    """Serialize an IRI as an ``IRIREF`` token of canonical N-Quads."""
    out: List[str] = []
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise RDFCanonicalizationError(
                f"cannot canonicalize quad: {position} contains the unpaired "
                f"surrogate U+{code:04X} ({value!r})"
            )
        if code <= 0x20 or char in '<>"{}|^`\\':
            out.append(f"\\u{code:04X}" if code <= 0xFFFF else f"\\U{code:08X}")
        else:
            out.append(char)
    return "".join(out)


def _rdfc_escape_literal(value: str) -> str:
    """Serialize a literal's lexical form as the body of ``STRING_LITERAL_QUOTE``."""
    out: List[str] = []
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise RDFCanonicalizationError(
                "cannot canonicalize quad: object literal contains the "
                f"unpaired surrogate U+{code:04X}"
            )
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif code <= 0x1F or code == 0x7F:
            out.append(f"\\u{code:04X}")
        elif code in (0xFFFE, 0xFFFF):
            # Not a valid XML 1.1 Char, so canonical N-Quads requires UCHAR.
            out.append(f"\\u{code:04X}")
        else:
            out.append(char)
    return "".join(out)


def _rdfc_iri_token(term: Node, position: str) -> str:
    if not isinstance(term, URIRef):
        raise RDFCanonicalizationError(
            f"cannot canonicalize quad: {position} is a "
            f"{_rdfc_term_kind(term)} ({term!r}), expected an IRI"
        )
    value = str(term)
    _rdfc_check_absolute_iri(value, position)
    return f"<{_rdfc_escape_iri(value, position)}>"


def _rdfc_literal_token(term: Literal) -> str:
    body = _rdfc_escape_literal(str(term))
    if term.language:
        return f'"{body}"@{term.language}'
    datatype = term.datatype
    if datatype is None:
        return f'"{body}"'
    datatype_value = str(datatype)
    # Canonical N-Quads drops an explicit xsd:string datatype.  This does not
    # lose information: RDF 1.1 says simple literals ARE xsd:string literals,
    # and RDFLib keeps them distinct only as an implementation detail.
    if datatype_value == _XSD_STRING:
        return f'"{body}"'
    if datatype_value == _RDF_LANG_STRING:
        raise RDFCanonicalizationError(
            "cannot canonicalize quad: object literal has datatype "
            f"rdf:langString but no language tag ({term!r})"
        )
    _rdfc_check_absolute_iri(datatype_value, "datatype")
    return f'"{body}"^^<{_rdfc_escape_iri(datatype_value, "datatype")}>'


def _rdfc_subject_token(term: Node, position: str) -> str:
    if isinstance(term, BNode):
        return "_:" + str(term)
    if isinstance(term, URIRef):
        value = str(term)
        _rdfc_check_absolute_iri(value, position)
        return f"<{_rdfc_escape_iri(value, position)}>"
    raise RDFCanonicalizationError(
        f"cannot canonicalize quad: {position} is a "
        f"{_rdfc_term_kind(term)} ({term!r}), expected an IRI or blank node"
    )


def _rdfc_graph_token(term: Node) -> str:
    if isinstance(term, BNode):
        return "_:" + str(term)
    if isinstance(term, URIRef):
        value = str(term)
        _rdfc_check_absolute_iri(value, "graph name")
        return f"<{_rdfc_escape_iri(value, 'graph name')}>"
    raise RDFCanonicalizationError(
        f"cannot canonicalize quad: graph name is a "
        f"{_rdfc_term_kind(term)} ({term!r}), expected an IRI or blank node"
    )


def _rdfc_validate_quad(quad: Tuple[Node, Node, Node, Optional[Node]]) -> None:
    """Check that every component of ``quad`` has an RDF 1.1 term type.

    Also serializes each component, which validates IRIs and literal text, so
    that every semantic error surfaces before any output is written.
    """
    subject, predicate, obj, graph_name = quad
    _rdfc_subject_token(subject, "subject")
    _rdfc_iri_token(predicate, "predicate")
    if isinstance(obj, Literal):
        _rdfc_literal_token(obj)
    else:
        _rdfc_subject_token(obj, "object")
    if graph_name is not None:
        _rdfc_graph_token(graph_name)


class _RDFCIdentifierIssuer:
    """Identifier issuer state (RDFC-1.0 section 4.3)."""

    __slots__ = ("prefix", "issued", "counter")

    def __init__(
        self, prefix: str, issued: Optional[Dict[str, str]] = None, counter: int = 0
    ):
        self.prefix = prefix
        self.issued: Dict[str, str] = {} if issued is None else dict(issued)
        self.counter = counter

    def copy(self) -> _RDFCIdentifierIssuer:
        return _RDFCIdentifierIssuer(self.prefix, self.issued, self.counter)

    def issue(self, existing: str) -> str:
        """Issue Identifier algorithm (section 4.5)."""
        issued = self.issued.get(existing)
        if issued is not None:
            return issued
        issued = f"{self.prefix}{self.counter}"
        self.issued[existing] = issued
        self.counter += 1
        return issued

    def has(self, existing: str) -> bool:
        return existing in self.issued

    def issued_order(self) -> List[str]:
        return list(self.issued)


class _RDFCNdegreeFrame:
    """One activation record of the recast Hash N-Degree Quads algorithm.

    The fields mirror the local variables of section 4.8.3:

    * ``related_groups`` is ``Hn``, sorted by related hash;
    * ``data`` is "data to hash";
    * ``chosen_path`` / ``chosen_issuer`` belong to the group being searched;
    * ``issuer_copy``, ``path`` and ``recursion_list`` belong to the
      permutation being evaluated.

    ``step`` advances until it either needs a child call (returning a new frame
    for the caller to push) or finishes (``done`` set).
    """

    __slots__ = (
        "identifier",
        "issuer",
        "related_groups",
        "group_index",
        "data",
        "chosen_path",
        "chosen_issuer",
        "permutations",
        "issuer_copy",
        "path",
        "recursion_list",
        "recursion_index",
        "skipped",
        "done",
        "result_hash",
        "result_issuer",
    )

    def __init__(
        self,
        identifier: str,
        issuer: _RDFCIdentifierIssuer,
        hash_to_related: Dict[str, List[str]],
    ):
        self.identifier = identifier
        self.issuer = issuer
        self.related_groups = [
            (related_hash, hash_to_related[related_hash])
            for related_hash in sorted(hash_to_related)
        ]
        self.group_index = 0
        self.data: List[str] = []
        self.chosen_path = ""
        self.chosen_issuer: Optional[_RDFCIdentifierIssuer] = None
        self.permutations: Optional[Iterator[Tuple[str, ...]]] = None
        self.issuer_copy: Optional[_RDFCIdentifierIssuer] = None
        self.path = ""
        self.recursion_list: List[str] = []
        self.recursion_index = 0
        self.skipped = False
        self.done = False
        self.result_hash = ""
        self.result_issuer: Optional[_RDFCIdentifierIssuer] = None

    def _too_long(self, path: str) -> bool:
        return bool(
            self.chosen_path
            and len(path) >= len(self.chosen_path)
            and path > self.chosen_path
        )

    def receive_child(
        self, result_hash: str, result_issuer: _RDFCIdentifierIssuer
    ) -> None:
        """Continue a permutation after a recursive call returned."""
        assert self.issuer_copy is not None
        related = self.recursion_list[self.recursion_index]
        self.path += "_:" + self.issuer_copy.issue(related)
        self.path += f"<{result_hash}>"
        self.issuer_copy = result_issuer
        self.recursion_index += 1
        if self._too_long(self.path):
            self.skipped = True

    def step(
        self, canonicalizer: _RDFCDatasetCanonicalizer
    ) -> Optional[_RDFCNdegreeFrame]:
        """Advance the frame; return a child frame if recursion is needed."""
        if self.done:
            return None
        if self.permutations is None:
            # Start the next related-hash group, or finish the frame.
            if self.group_index >= len(self.related_groups):
                self.result_hash = sha256(
                    "".join(self.data).encode("utf-8")
                ).hexdigest()
                self.result_issuer = self.issuer
                self.done = True
                return None
            _related_hash, related_list = self.related_groups[self.group_index]
            self.data.append(_related_hash)
            self.chosen_path = ""
            self.chosen_issuer = None
            self.permutations = _rdfc_permutations(related_list)
            return None

        if self.issuer_copy is None:
            # Begin the next permutation.  If the group is exhausted, adopt
            # the chosen path/issuer and move on to the next group.
            permutation = next(self.permutations, None)
            if permutation is None:
                if self.chosen_issuer is None:
                    raise AssertionError(
                        "RDFC-1.0: a related hash had no permutation to choose " "from"
                    )
                self.data.append(self.chosen_path)
                self.issuer = self.chosen_issuer
                self.group_index += 1
                self.permutations = None
                return None
            self.issuer_copy = self.issuer.copy()
            self.path = ""
            self.recursion_list = []
            self.recursion_index = 0
            self.skipped = False
            for related in permutation:
                if canonicalizer.canonical_issuer.has(related):
                    self.path += "_:" + canonicalizer.canonical_issuer.issue(related)
                else:
                    if not self.issuer_copy.has(related):
                        self.recursion_list.append(related)
                    self.path += "_:" + self.issuer_copy.issue(related)
                if self._too_long(self.path):
                    self.skipped = True
                    break
            if self.skipped:
                self.issuer_copy = None
            return None

        if self.skipped:
            self.issuer_copy = None
            return None

        if self.recursion_index < len(self.recursion_list):
            related = self.recursion_list[self.recursion_index]
            assert self.issuer_copy is not None
            return canonicalizer._new_ndegree_frame(related, self.issuer_copy)

        # Every recursive call has returned; compare and choose.
        if not self.chosen_path or self.path < self.chosen_path:
            self.chosen_path = self.path
            self.chosen_issuer = self.issuer_copy
        self.issuer_copy = None
        return None


class _RDFCDatasetCanonicalizer:
    """One RDFC-1.0 run over a fixed list of quads.

    Instances are single-use and hold no global state: repeated or
    interleaved runs cannot influence each other.
    """

    def __init__(self, quads: List[Tuple[Node, Node, Node, Optional[Node]]]):
        self.quads = quads
        self.blank_node_to_quads: Dict[
            str, List[Tuple[Node, Node, Node, Optional[Node]]]
        ] = defaultdict(list)
        for quad in quads:
            for component in (quad[0], quad[2], quad[3]):
                if isinstance(component, BNode):
                    self.blank_node_to_quads[str(component)].append(quad)
        self.canonical_issuer = _RDFCIdentifierIssuer(_RDFC_CANONICAL_PREFIX)
        self.first_degree_hashes: Dict[str, str] = {}
        # N-degree accounting is reset per symmetry group in `canonicalize`;
        # see `_rdfc_ndegree_budget` for why the budget is per group.
        self.ndegree_calls = 0
        self.ndegree_call_limit = _RDFC_NDEGREE_BUDGET_FLOOR

    def _nquad(
        self,
        quad: Tuple[Node, Node, Node, Optional[Node]],
        label_for: Callable[[str], str],
        reference: Optional[str] = None,
    ) -> str:
        """Serialize one quad in canonical N-Quads form.

        With ``reference`` set, every blank node becomes ``_:a`` when it is the
        reference node and ``_:z`` otherwise, as required by the Hash First
        Degree Quads algorithm (section 4.6).
        """

        def blank(term: BNode) -> str:
            if reference is not None:
                return "_:a" if str(term) == reference else "_:z"
            return "_:" + label_for(str(term))

        subject, predicate, obj, graph_name = quad
        if isinstance(subject, BNode):
            subject_token = blank(subject)
        else:
            subject_token = _rdfc_subject_token(subject, "subject")
        predicate_token = _rdfc_iri_token(predicate, "predicate")
        if isinstance(obj, BNode):
            object_token = blank(obj)
        elif isinstance(obj, Literal):
            object_token = _rdfc_literal_token(obj)
        else:
            object_token = _rdfc_subject_token(obj, "object")
        tokens = [subject_token, predicate_token, object_token]
        if graph_name is not None:
            tokens.append(
                blank(graph_name)
                if isinstance(graph_name, BNode)
                else _rdfc_graph_token(graph_name)
            )
        return " ".join(tokens) + " .\n"

    # -- section 4.6 ----------------------------------------------------------

    def _hash_first_degree_quads(self, identifier: str) -> str:
        cached = self.first_degree_hashes.get(identifier)
        if cached is not None:
            return cached
        lines = [
            self._nquad(quad, str, reference=identifier)
            for quad in self.blank_node_to_quads[identifier]
        ]
        lines.sort()
        digest = sha256("".join(lines).encode("utf-8")).hexdigest()
        self.first_degree_hashes[identifier] = digest
        return digest

    # -- section 4.7 ----------------------------------------------------------

    def _hash_related_blank_node(
        self,
        related: str,
        quad: Tuple[Node, Node, Node, Optional[Node]],
        issuer: _RDFCIdentifierIssuer,
        position: str,
    ) -> str:
        data = position
        if position != "g":
            # The predicate is between angle brackets but its IRI is not
            # escaped: this matches the examples in section 4.7.2 and every
            # interoperable implementation.  It is an input to a SHA-256
            # digest, never emitted as N-Quads.
            data += f"<{quad[1]}>"
        if self.canonical_issuer.has(related):
            data += "_:" + self.canonical_issuer.issue(related)
        elif issuer.has(related):
            data += "_:" + issuer.issue(related)
        else:
            data += self._hash_first_degree_quads(related)
        return sha256(data.encode("utf-8")).hexdigest()

    def _related_by_hash(
        self, identifier: str, issuer: _RDFCIdentifierIssuer
    ) -> Dict[str, List[str]]:
        """Group related blank nodes by the hash of section 4.7."""
        hash_to_related: Dict[str, List[str]] = defaultdict(list)
        for quad in self.blank_node_to_quads[identifier]:
            for component, position in (
                (quad[0], "s"),
                (quad[2], "o"),
                (quad[3], "g"),
            ):
                if isinstance(component, BNode) and str(component) != identifier:
                    related = str(component)
                    hash_to_related[
                        self._hash_related_blank_node(related, quad, issuer, position)
                    ].append(related)
        return hash_to_related

    # -- section 4.8 ----------------------------------------------------------

    def _hash_n_degree_quads(
        self, identifier: str, issuer: _RDFCIdentifierIssuer
    ) -> Tuple[str, _RDFCIdentifierIssuer]:
        """Run RDFC-1.0 section 4.8 without Python call recursion.

        A literal recursive implementation overflows Python's stack on
        moderately deep gossip paths (a chain of a few hundred blank nodes is
        enough).  The algorithm's recursion is "call for each related node,
        then continue with the returned issuer", so it is expressed here with
        an explicit stack.  Each stack entry is a *frame* that walks its own
        related-hash groups; when it reaches a related node that must be
        recursed into, it pushes a child frame and, once that child returns,
        resumes exactly where the recursive version would have.
        """
        root_frame = self._new_ndegree_frame(identifier, issuer)
        stack: List[_RDFCNdegreeFrame] = [root_frame]
        while stack:
            frame = stack[-1]
            child = frame.step(self)
            if child is not None:
                stack.append(child)
                continue
            if frame.done:
                stack.pop()
                result_issuer = frame.result_issuer
                assert result_issuer is not None
                if stack:
                    stack[-1].receive_child(frame.result_hash, result_issuer)
                    continue
                return frame.result_hash, result_issuer
        raise AssertionError("RDFC-1.0: N-degree traversal produced no result")

    def _new_ndegree_frame(
        self, identifier: str, issuer: _RDFCIdentifierIssuer
    ) -> _RDFCNdegreeFrame:
        self.ndegree_calls += 1
        if self.ndegree_calls > self.ndegree_call_limit:
            raise RDFCanonicalizationError(
                "cannot canonicalize dataset: RDFC-1.0 exceeded "
                f"{self.ndegree_call_limit} Hash N-Degree Quads invocations. "
                "The dataset contains a blank node component that is too "
                "highly symmetric to canonicalize; see RDFC-1.0 section 7.1 "
                "('Dataset Poisoning')."
            )
        return _RDFCNdegreeFrame(
            identifier, issuer, self._related_by_hash(identifier, issuer)
        )

    # -- section 4.4 ----------------------------------------------------------

    def canonicalize(self) -> str:
        hash_to_blank_nodes: Dict[str, List[str]] = defaultdict(list)
        for identifier in self.blank_node_to_quads:
            hash_to_blank_nodes[self._hash_first_degree_quads(identifier)].append(
                identifier
            )

        non_unique: List[List[str]] = []
        for first_degree_hash in sorted(hash_to_blank_nodes):
            identifiers = hash_to_blank_nodes[first_degree_hash]
            if len(identifiers) > 1:
                non_unique.append(identifiers)
            else:
                self.canonical_issuer.issue(identifiers[0])

        for identifiers in non_unique:
            # The budget applies to the whole group and is reset between
            # groups, so a small symmetric component is judged on its own size
            # even when the surrounding dataset is large.  A group needs at
            # most O(group_size) root calls and each may recurse through the
            # group's other members; quadratic covers cycles and chains while
            # still refusing factorial-sized permutation searches.
            self.ndegree_calls = 0
            self.ndegree_call_limit = _rdfc_ndegree_budget(len(identifiers))
            hash_path_list: List[Tuple[str, _RDFCIdentifierIssuer]] = []
            for identifier in identifiers:
                if self.canonical_issuer.has(identifier):
                    continue
                issuer = _RDFCIdentifierIssuer(_RDFC_TEMPORARY_PREFIX)
                issuer.issue(identifier)
                hash_path_list.append(self._hash_n_degree_quads(identifier, issuer))
            hash_path_list.sort(key=lambda item: item[0])
            for _result_hash, result_issuer in hash_path_list:
                for existing in result_issuer.issued_order():
                    self.canonical_issuer.issue(existing)

        lines = [self._nquad(quad, self.canonical_issuer.issue) for quad in self.quads]
        lines.sort()
        return "".join(lines)


def _rdfc_permutations(items: List[str]) -> Iterator[Tuple[str, ...]]:
    """Yield permutations of ``items`` lazily in lexicographic order.

    RDFC-1.0 step 5.4.4 visits every permutation of a related blank node
    group, but the chosen path is the least one, so visiting them in
    lexicographic order lets the early-exit comparisons prune most of the
    search.  Duplicates (two quads yielding the same related node under the
    same hash) are collapsed first: RDFC-1.0 treats the group as a set of
    identifiers, and permuting identical entries cannot change the outcome.
    """
    unique = sorted(set(items))
    return itertools.permutations(unique)


def _rdfc_context_identifier(context: Any) -> Node:
    """Reduce a store context to its graph name (a URIRef or BNode).

    RDFLib's stores normally yield [`Graph`][rdflib.graph.Graph] objects, but
    simple stores can also yield bare identifiers; both forms are accepted.
    """
    identifier = context.identifier if isinstance(context, Graph) else context
    if isinstance(identifier, (URIRef, BNode)):
        return identifier
    raise RDFCanonicalizationError(
        "cannot canonicalize quad: graph name cannot be determined from "
        f"store context {context!r}"
    )


def _rdfc_collect_quads(
    dataset: Dataset,
) -> List[Tuple[Node, Node, Node, Optional[Node]]]:
    """Read the dataset as a set of quads.

    The default graph is identified by its graph identifier, not by object
    identity, and that identifier is replaced by ``None`` so it can never leak
    into the output.  A statement repeated in one graph appears once; the same
    statement in two graphs appears once per graph.  Empty named graphs never
    reach this function, because a store only yields statements.
    """
    quads: List[Tuple[Node, Node, Node, Optional[Node]]] = []
    seen: Set[Tuple[Node, Node, Node, Optional[Node]]] = set()
    for (subject, predicate, obj), contexts in dataset.store.triples(
        (None, None, None), context=None
    ):
        context_identifiers = {_rdfc_context_identifier(c) for c in contexts}
        if not context_identifiers:
            graph_names: Set[Optional[Node]] = {None}
        elif DATASET_DEFAULT_GRAPH_ID in context_identifiers:
            graph_names = {None}
            graph_names.update(context_identifiers - {DATASET_DEFAULT_GRAPH_ID})
        else:
            graph_names = set(context_identifiers)
        for graph_name in graph_names:
            quad = (subject, predicate, obj, graph_name)
            if quad in seen:
                continue
            seen.add(quad)
            quads.append(quad)
    return quads


def canonicalize_dataset(dataset: Dataset) -> bytes:
    """Return the RDFC-1.0 canonical N-Quads serialization of a dataset.

    The result is the UTF-8 encoded canonical N-Quads document defined by the
    W3C Recommendation [RDF Dataset Canonicalization][RDFC-1.0] of 2024-05-21,
    using SHA-256.  Two datasets serialize to the same bytes exactly when they
    are isomorphic *as datasets*: blank node labels, insertion order, prefix
    bindings and the [`default_union`][rdflib.graph.Dataset.default_union]
    flag do not affect the output, while which graph a quad belongs to does.

    The default graph, IRI-named graphs and blank-node-named graphs are
    treated as one dataset.  A blank node shared between the default graph and
    a named graph, or used both as a graph name and as a subject, keeps a
    single identity.  Empty named graphs contribute no output.

    Terms are used exactly as stored: literals are not normalized and equal
    values with different lexical forms stay distinct, no Unicode
    normalization is applied, and IRIs are written verbatim (escaped as
    canonical N-Quads requires).

    Args:
        dataset: The [`Dataset`][rdflib.graph.Dataset] to canonicalize.  Its
            quads, node identities, graph list, prefix bindings and
            ``default_union`` setting are not modified.  The dataset must not
            be modified concurrently while this function runs.

    Returns:
        The canonical N-Quads document as UTF-8 bytes, or ``b""`` for an empty
        dataset.

    Raises:
        TypeError: If ``dataset`` is not an [`rdflib.Dataset`][rdflib.graph.Dataset].
        RDFCanonicalizationError: If a quad cannot be represented as RDF 1.1 /
            canonical N-Quads (a literal predicate, a relative IRI, an
            unpaired surrogate, ...), or if the dataset is too highly
            symmetric to canonicalize.

    [RDFC-1.0]: https://www.w3.org/TR/2024/REC-rdf-canon-20240521/
    """
    if not isinstance(dataset, Dataset):
        raise TypeError(
            "canonicalize_dataset() requires an rdflib.Dataset, got "
            f"{type(dataset).__name__}"
        )
    quads = _rdfc_collect_quads(dataset)
    for quad in quads:
        _rdfc_validate_quad(quad)
    if not quads:
        return b""
    return _RDFCDatasetCanonicalizer(quads).canonicalize().encode("utf-8")
