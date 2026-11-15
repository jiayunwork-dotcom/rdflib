import rdflib.plugin
from rdflib import ConjunctiveGraph, Dataset, URIRef


def test_finalnewline():
    """
    http://code.google.com/p/rdflib/issues/detail?id=5
    """

    graph = ConjunctiveGraph()
    graph.add(
        (
            URIRef("http://ex.org/a"),
            URIRef("http://ex.org/b"),
            URIRef("http://ex.org/c"),
        )
    )
    dataset = Dataset()
    dataset.add(
        (
            URIRef("http://ex.org/a"),
            URIRef("http://ex.org/b"),
            URIRef("http://ex.org/c"),
        )
    )

    # Formats that canonicalize a whole RDF dataset rather than a graph.
    dataset_only_formats = {"rdfc-1.0", "application/rdfc-1.0"}

    failed = set()
    for p in rdflib.plugin.plugins(None, rdflib.plugin.Serializer):
        target = dataset if p.name in dataset_only_formats else graph
        v = target.serialize(format=p.name, encoding="utf-8")
        lines = v.split(b"\n")
        if b"\n" not in v or (lines[-1] != b""):
            failed.add(p.name)
    # JSON-LD does not require a final newline (because JSON doesn't)
    failed = failed.difference({"json-ld", "application/ld+json"})
    assert len(failed) == 0, "No final newline for formats: '%s'" % failed
