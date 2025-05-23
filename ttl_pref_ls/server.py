"""ttl_pref_ls.server
~~~~~~~~~~~~~~~~~~~~~
Turtle‑aware language server that provides
* hover with `skos:prefLabel`
* Hint diagnostics for resources missing a label (all occurrences)
* inline inlay‑hints (virtual text) showing the prefLabel next to each IRI/QName
* resolves missing prefLabels from remote namespace documents via aiohttp
"""
from __future__ import annotations

import logging
import sys
from typing import Dict, List, Callable

from pygls.server import LanguageServer
from rdflib import URIRef
from rdflib.namespace import RDF
from lsprotocol import types

from .indexer import build as build_index, DocumentIndex
from .resolver import maybe_resolve

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(levelname)s %(asctime)s [%(name)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ttl_pref_ls")

# ---------------------------------------------------------------------------
# Small utils
# ---------------------------------------------------------------------------

def _pretty_iri(idx: DocumentIndex, iri: str) -> str:
    for pref, ns in idx.prefixes.items():
        if iri.startswith(ns):
            return f"{pref}:{iri[len(ns):]}"
    return f"<{iri}>"  # fallback


def _ns_base(idx: DocumentIndex, iri: str) -> str:
    """Return namespace base using declared prefixes when possible.

    1. If the IRI starts with any declared namespace in ``idx.prefixes``
       return that namespace string.
    2. Otherwise fall back to slicing at the last '#' or '/'.
    """
    for ns in idx.prefixes.values():
        if iri.startswith(ns):
            return ns
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[0] + sep
    return iri

# ---------------------------------------------------------------------------
# Language‑server class
# ---------------------------------------------------------------------------

class TurtlePrefLanguageServer(LanguageServer):
    """One LSP instance per Neovim client/workspace."""

    def __init__(self) -> None:
        super().__init__("ttl-pref-ls", "0.2.0")
        self._documents: Dict[str, DocumentIndex] = {}


ls = TurtlePrefLanguageServer()

# ---------------------------------------------------------------------------
# INITIALIZE
# ---------------------------------------------------------------------------

@ls.feature(types.INITIALIZE)
def on_initialize(ls: TurtlePrefLanguageServer, params: types.InitializeParams):
    log.info("initialize: client %s", params.client_info)

    capabilities = types.ServerCapabilities(
        hover_provider=True,
        inlay_hint_provider=True,
        text_document_sync=types.TextDocumentSyncOptions(
            open_close=True,
            change=types.TextDocumentSyncKind.Full,
            save=None,
        ),
    )
    server_info = types.InitializeResultServerInfoType(name=ls.name, version=ls.version)
    return types.InitializeResult(capabilities=capabilities, server_info=server_info)

# ---------------------------------------------------------------------------
# Document lifecycle helpers
# ---------------------------------------------------------------------------

def _on_remote_labels(new_pairs: Dict[str, str]):
    """Callback from resolver – merge labels & refresh UI."""
    if not new_pairs:
        return
    for doc_idx in ls._documents.values():
        for iri, lab in new_pairs.items():
            doc_idx.labels[URIRef(iri)] = lab
    # Re‑publish diagnostics & notify inlay‑hints refresh
    for uri, idx in ls._documents.items():
        _publish_diagnostics(ls, uri, idx)
    ls.send_notification("$/refreshInlayHints", {})


def _index_and_store(ls: TurtlePrefLanguageServer, uri: str, text: str):
    """(Re)build the index; swallow parser errors so edits never crash LSP."""
    try:
        idx = build_index(text)
    except Exception as exc:
        log.debug("parse error ignored: %s", exc)
        return

    ls._documents[uri] = idx
    _publish_diagnostics(ls, uri, idx)

# didOpen --------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: TurtlePrefLanguageServer, params: types.DidOpenTextDocumentParams):
    _index_and_store(ls, params.text_document.uri, params.text_document.text)

# didChange ------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: TurtlePrefLanguageServer, params: types.DidChangeTextDocumentParams):
    try:
        full_text = ls.workspace.get_document(params.text_document.uri).source
    except Exception:
        return
    _index_and_store(ls, params.text_document.uri, full_text)

# ---------------------------------------------------------------------------
# HOVER (unchanged except version bump)
# ---------------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_HOVER)
def hover(ls: TurtlePrefLanguageServer, params: types.HoverParams):
    idx = ls._documents.get(params.text_document.uri)
    if idx is None:
        return None

    pos = params.position
    iri = idx.iri_at(pos.line, pos.character)

    # special‑case literal "a"
    if iri is None:
        try:
            line_txt = ls.workspace.get_document(params.text_document.uri).lines[pos.line]
        except Exception:
            line_txt = ""
        col = pos.character
        start = col
        end = col
        while start > 0 and line_txt[start - 1].isalnum():
            start -= 1
        while end < len(line_txt) and line_txt[end].isalnum():
            end += 1
        token = line_txt[start:end]
        if token == "a":
            iri = RDF.type
            range_override = types.Range(
                start=types.Position(line=pos.line, character=start),
                end=types.Position(line=pos.line, character=end),
            )
        else:
            return None
    else:
        range_override = None

    label = idx.labels.get(iri)
    display = _pretty_iri(idx, str(iri))
    full_uri = f"<{iri}>"

    if label:
        md = f"{full_uri}\n`{display}`\n**prefLabel:** {label}"
    else:
        md = f"{full_uri}\n`{display}`"

    return types.Hover(contents=types.MarkupContent(kind="markdown", value=md), range=range_override)

# ---------------------------------------------------------------------------
# DIAGNOSTICS  – underline **every** occurrence of unlabeled IRIs/QNames
# ---------------------------------------------------------------------------

def _publish_diagnostics(ls: LanguageServer, uri: str, idx: DocumentIndex):
    diags: List[types.Diagnostic] = []

    for iri in idx.uris:
        if iri in idx.labels:
            continue
        ns = _ns_base(idx, str(iri))
        # only test for EMMO
        if ns != "https://w3id.org/emmo#":
            continue
        # queue remote resolution once per namespace
        maybe_resolve(ns, _on_remote_labels)

        str_iri = str(iri)
        for line, ranges in idx.ranges.items():
            for start, end, token_iri in ranges:
                if token_iri != iri:
                    continue
                diags.append(
                    types.Diagnostic(
                        range=types.Range(
                            start=types.Position(line=line, character=start),
                            end=types.Position(line=line, character=end),
                        ),
                        severity=types.DiagnosticSeverity.Hint,
                        source="ttl-pref-ls",
                        message=f"No skos:prefLabel found for {_pretty_iri(idx, str_iri)}",
                    )
                )

    ls.publish_diagnostics(uri, diags)

# ---------------------------------------------------------------------------
# INLAY‑HINTS (unchanged)
# ---------------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_INLAY_HINT)
def inlay_hint(ls: TurtlePrefLanguageServer, params: types.InlayHintParams):
    idx = ls._documents.get(params.text_document.uri)
    if idx is None:
        return None

    hints: List[types.InlayHint] = []
    for line in range(params.range.start.line, params.range.end.line + 1):
        for start, end, iri in idx.ranges.get(line, []):
            label = idx.labels.get(iri)
            if not label:
                continue
            hints.append(types.InlayHint(position=types.Position(line=line, character=end), label=label, padding_left=True))
    return hints

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def start_io() -> None:
    """Run the language server on stdio (for Neovim)."""
    ls.start_io(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    start_io()
