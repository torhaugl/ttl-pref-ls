"""ttl_pref_ls.indexer
~~~~~~~~~~~~~~~~~~~~
Builds an in‑memory index for **one Turtle document** so the language‑server
can provide:
* hover / inlay‑hint look‑ups (`URIRef → prefLabel`)
* diagnostics for resources missing a `skos:prefLabel`
* text‑ranges of every absolute IRI **and** QName appearing in the file

The indexer is intentionally stateless and side‑effect‑free so unit tests can
feed raw strings and assert on the returned `DocumentIndex`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Set, Optional
import re

from rdflib import Graph, URIRef, Namespace
from rdflib.namespace import SKOS

# ---------------------------------------------------------------------------
# Public data container
# ---------------------------------------------------------------------------

@dataclass
class DocumentIndex:
    """All information the server needs for one buffer."""

    # Static RDF facts -------------------------------------------------------
    labels: Dict[URIRef, str] = field(default_factory=dict)   # absolute IRI → prefLabel text
    uris:   Set[URIRef]       = field(default_factory=set)    # every absolute IRI seen
    prefixes: Dict[str, str]  = field(default_factory=dict)   # prefix → namespace (as str)

    # Lexical locations ------------------------------------------------------
    # Each line number (0‑based) maps to a list of (start, end, absolute IRI)
    ranges: Dict[int, List[Tuple[int, int, URIRef]]] = field(default_factory=dict)
    first_pos: Dict[URIRef, Tuple[int, int]] = field(default_factory=dict)  # absolute IRI → (line, start‑char)

    # ---------------------------------------------------------------------
    # helpers for the LSP server (kept here for cohesion)
    # ---------------------------------------------------------------------
    def iri_at(self, line: int, char: int) -> Optional[URIRef]:
        """Return the URI whose token spans (*line*, *char*), or ``None``."""
        for start, end, uri in self.ranges.get(line, []):
            if start <= char < end:
                return uri
        return None

# ---------------------------------------------------------------------------
# Regexes – compiled once at import time
# ---------------------------------------------------------------------------

IRI_RE   = re.compile(r"<([^>]+)>")
QNAME_RE = re.compile(r"([A-Za-z][\w\-]*)\:([A-Za-z_][\w\-.]*)")

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build(text: str) -> DocumentIndex:
    """Parse *text* (Turtle) and return a fully‑populated :class:`DocumentIndex`."""

    idx = DocumentIndex()

    # ---- Stage 1: semantic graph -----------------------------------------
    g = Graph()
    g.parse(data=text, format="turtle")

    # 1a) Capture explicit @prefix declarations BEFORE iterating triples so
    #     QName resolution can rely on them.
    idx.prefixes = {p: str(ns) for p, ns in g.namespaces()}

    # 1b) Iterate triples once to collect URIs & prefLabels
    for s, p, o in g:
        if isinstance(s, URIRef):
            idx.uris.add(s)
        if isinstance(o, URIRef):
            idx.uris.add(o)
        if p == SKOS.prefLabel and isinstance(s, URIRef) and o.datatype is None:
            idx.labels[s] = str(o)

    # ---- Stage 2: lexical pass -------------------------------------------
    # We need character ranges because rdflib discards source positions.

    for lineno, line in enumerate(text.splitlines()):
        # 2a) Absolute IRIs <...>
        for m in IRI_RE.finditer(line):
            _store_match(idx, lineno, m.start(0), m.end(0), URIRef(m.group(1)))

        # 2b) QNames prefix:local – capture only if prefix is defined
        for m in QNAME_RE.finditer(line):
            prefix = m.group(1)
            local  = m.group(2)
            ns = idx.prefixes.get(prefix)
            if ns is None:
                # Undefined prefix – we *could* emit a diagnostic later; just ignore for now
                continue
            iri = URIRef(ns + local)
            _store_match(idx, lineno, m.start(0), m.end(0), iri)

    return idx

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _store_match(idx: DocumentIndex, line: int, start: int, end: int, iri: URIRef) -> None:
    """Record lexical location and first‑occurrence for *iri*."""
    idx.ranges.setdefault(line, []).append((start, end, iri))
    idx.first_pos.setdefault(iri, (line, start))
    # (URIs set is already filled by graph.parse; adding again is harmless but avoided.)
