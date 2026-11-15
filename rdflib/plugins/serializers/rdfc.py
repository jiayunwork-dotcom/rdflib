"""RDFC-1.0 canonical N-Quads serializer.

The algorithm itself is [`rdflib.compare.canonicalize_dataset`][]; this
module adapts it to the [`Serializer`][rdflib.serializer.Serializer] plugin
interface.  [`Dataset.serialize`][rdflib.graph.Dataset.serialize] intercepts
the ``rdfc-1.0`` format before a destination is opened, so that argument and
dataset errors leave the destination untouched; this class is also the plugin
entry point, which makes ``Graph.serialize(format="rdfc-1.0")`` fail with a
clear message instead of silently producing a triple-only document.
"""

from __future__ import annotations

from typing import IO, Any, Optional

from rdflib.compare import canonicalize_dataset
from rdflib.graph import Dataset
from rdflib.serializer import Serializer

__all__ = ["RDFCSerializer"]


class RDFCSerializer(Serializer):
    """Serializer for the ``rdfc-1.0`` format: canonical N-Quads.

    Instantiating it with anything other than a [`Dataset`][rdflib.graph.Dataset]
    raises [`TypeError`][].
    """

    def __init__(self, store: Any):
        if not isinstance(store, Dataset):
            raise TypeError(
                "the 'rdfc-1.0' format canonicalizes an RDF dataset and "
                f"requires an rdflib.Dataset, not {type(store).__name__}"
            )
        super(RDFCSerializer, self).__init__(store)
        self.store: Dataset

    def serialize(
        self,
        stream: IO[bytes],
        base: Optional[str] = None,
        encoding: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Write the canonical N-Quads document to ``stream``.

        Args:
            stream: A binary stream to write UTF-8 bytes to.
            base: Must be ``None``; canonical N-Quads has no relative IRIs.
            encoding: Must be ``None`` or UTF-8; canonical N-Quads is UTF-8.

        Raises:
            ValueError: If ``base`` is not ``None`` or ``encoding`` is not UTF-8.
            RDFCanonicalizationError: If the dataset cannot be canonicalized.
        """
        if base is not None:
            raise ValueError(
                "the 'rdfc-1.0' format does not support a base IRI; "
                "canonical N-Quads contains only absolute IRIs"
            )
        if encoding is not None and encoding.lower() not in ("utf-8", "utf8"):
            raise ValueError(
                "the 'rdfc-1.0' format is UTF-8 only, got encoding " f"{encoding!r}"
            )
        stream.write(canonicalize_dataset(self.store))
