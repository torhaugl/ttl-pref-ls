"""ttl_pref_ls.resolver
~~~~~~~~~~~~~~~~~~~~~~
Asynchronous *namespace resolver* that fetches remote Turtle/RDF documents to
retrieve missing ``skos:prefLabel`` values.

Usage (from server.py)::

    from .resolver import maybe_resolve

    def _index_and_store(...):
        ...
        if unlabeled_iri:
            ns_base = _get_ns_base(unlabeled_iri)  # e.g. "https://w3id.org/emmo#"
            maybe_resolve(ns_base, _on_labels)

    def _on_labels(new_pairs: dict[str, str]):
        # merge into DocumentIndex.labels and trigger UI refresh
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Callable, Set, Optional

from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

try:
    import aiohttp  # type: ignore
except ImportError:  # pragma: no cover – fallback when aiohttp isn’t installed
    aiohttp = None  # noqa: N816 – handle below

__all__ = ["maybe_resolve"]

log = logging.getLogger("ttl_pref_ls.resolver")

# ---------------------------------------------------------------------------
# In‑memory caches (per language‑server process)
# ---------------------------------------------------------------------------

_remote_labels: Dict[str, str] = {}            # absolute IRI → label
_pending_tasks: Dict[str, asyncio.Task] = {}   # ns_base → running task
_failed_namespaces: Set[str] = set()           # ns_base that timed‑out / 4xx

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def maybe_resolve(ns_base: str, on_labels: Callable[[Dict[str, str]], None]) -> None:
    """Start resolving *ns_base* in the background (if not done already).

    ``on_labels`` will be called **once** with ``{iri: label}`` pairs – empty if
    the fetch failed.
    """
    if ns_base in _failed_namespaces:
        return
    if ns_base in _pending_tasks:
        return

    loop = asyncio.get_running_loop()
    task = loop.create_task(_worker(ns_base, on_labels))
    _pending_tasks[ns_base] = task

# ---------------------------------------------------------------------------
# Internal async worker
# ---------------------------------------------------------------------------

async def _worker(ns_base: str, cb: Callable[[Dict[str, str]], None]):
    """Fetch, parse, cache; always invoke *cb*.

    Removes its task from ``_pending_tasks`` when done.
    """
    try:
        labels = await _fetch_labels(ns_base)
        if labels:
            _remote_labels.update(labels)
        else:
            _failed_namespaces.add(ns_base)
    except Exception as exc:  # network/parse errors
        log.debug("resolver: %s failed – %s", ns_base, exc)
        _failed_namespaces.add(ns_base)
        labels = {}
    finally:
        _pending_tasks.pop(ns_base, None)
        try:
            cb(labels)
        except Exception as exc:
            log.debug("resolver callback error: %s", exc)

# ---------------------------------------------------------------------------
# Low‑level fetch + parse
# ---------------------------------------------------------------------------

_ACCEPT_HDR = (
    "text/turtle, application/rdf+xml, application/n‑quads; q=0.9, */*; q=0.1"
)
_TIMEOUT = 2.0  # seconds

async def _fetch_labels(ns_base: str) -> Dict[str, str]:
    """Return ``{absolute_iri: label}`` for one namespace URI, or {} on failure."""

    if aiohttp is None:
        log.debug("aiohttp missing – skipping remote resolve")
        return {}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(ns_base, headers={"Accept": _ACCEPT_HDR}, timeout=_TIMEOUT) as resp:
                if resp.status >= 400:
                    log.debug("HTTP %s on %s", resp.status, ns_base)
                    return {}
                data = await resp.text()
        except Exception as exc:
            log.debug("HTTP error on %s – %s", ns_base, exc)
            return {}

    # Parse with rdflib (blocking but quick for typical vocab size)
    g = Graph()
    try:
        g.parse(data=data, format="turtle")
    except Exception:
        try:
            g.parse(data=data, format="xml")
        except Exception as exc:
            log.debug("rdflib parse failed for %s – %s", ns_base, exc)
            return {}

    labels: Dict[str, str] = {}
    for s, p, o in g.triples((None, SKOS.prefLabel, None)):
        if isinstance(s, URIRef) and o.datatype is None:
            labels[str(s)] = str(o)

    return labels
