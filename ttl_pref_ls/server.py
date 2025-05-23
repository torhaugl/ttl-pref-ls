"""ttl_pref_ls.server
~~~~~~~~~~~~~~~~~~~~~
Turtle-aware language server that provides
* hover with `skos:prefLabel`
* Hint diagnostics for resources missing a label (now **all occurrences**)
* inline inlay-hints (virtual text) showing the prefLabel next to each IRI/QName

It re-uses the **indexer** for all semantic & lexical data.
"""
from __future__ import annotations

import logging
import sys
from typing import Dict, List

from pygls.server import LanguageServer
from lsprotocol import types

from .indexer import build as build_index, DocumentIndex

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(levelname)s %(asctime)s [%(name)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ttl_pref_ls")

# ---------------------------------------------------------------------------
# Small util: pretty-print an absolute IRI as QName if a prefix matches
# ---------------------------------------------------------------------------

def _pretty_iri(idx: DocumentIndex, iri: str) -> str:
    for pref, ns in idx.prefixes.items():
        if iri.startswith(ns):
            return f"{pref}:{iri[len(ns):]}"
    return f"<{iri}>"  # fallback

# ---------------------------------------------------------------------------
# Language-server class
# ---------------------------------------------------------------------------

class TurtlePrefLanguageServer(LanguageServer):
    """One LSP instance per Neovim client/workspace."""

    def __init__(self) -> None:
        super().__init__("ttl-pref-ls", "0.1.1")
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
    )
    server_info = types.InitializeResultServerInfoType(name=ls.name, version=ls.version)
    return types.InitializeResult(capabilities=capabilities, server_info=server_info)

# ---------------------------------------------------------------------------
# Document lifecycle helpers
# ---------------------------------------------------------------------------

def _index_and_store(ls: TurtlePrefLanguageServer, uri: str, text: str):
    idx = build_index(text)
    ls._documents[uri] = idx
    log.info("indexed %d URIs / %d prefLabels (%s)", len(idx.uris), len(idx.labels), uri)
    _publish_diagnostics(ls, uri, idx)

# didOpen --------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: TurtlePrefLanguageServer, params: types.DidOpenTextDocumentParams):
    _index_and_store(ls, params.text_document.uri, params.text_document.text)

# didChange (full-text) -------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: TurtlePrefLanguageServer, params: types.DidChangeTextDocumentParams):
    if not params.content_changes:
        return
    _index_and_store(ls, params.text_document.uri, params.content_changes[0].text)

# ---------------------------------------------------------------------------
# HOVER
# ---------------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_HOVER)
def hover(ls: TurtlePrefLanguageServer, params: types.HoverParams):
    idx = ls._documents.get(params.text_document.uri)
    if idx is None:
        return None

    pos = params.position
    iri = idx.iri_at(pos.line, pos.character)
    if iri is None:
        return None

    label = idx.labels.get(iri)
    if not label:
        return None  # no label ⇒ no hover

    display = _pretty_iri(idx, str(iri))
    md = f"**prefLabel:** {label}\n\n`{display}`"
    return types.Hover(contents=types.MarkupContent(kind="markdown", value=md))

# ---------------------------------------------------------------------------
# DIAGNOSTICS  – underline **every** occurrence of unlabeled IRIs/QNames
# ---------------------------------------------------------------------------

def _publish_diagnostics(ls: LanguageServer, uri: str, idx: DocumentIndex):
    diags: List[types.Diagnostic] = []

    for iri in idx.uris:
        if iri in idx.labels:
            continue  # labeled – skip

        str_iri = str(iri)
        # Walk every stored lexical range and add a diagnostic for each token that matches
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
                        message=f"No skos:prefLabel defined for {_pretty_iri(idx, str_iri)}",
                    )
                )

    ls.publish_diagnostics(uri, diags)

# ---------------------------------------------------------------------------
# INLAY-HINTS
# ---------------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_INLAY_HINT)
def inlay_hint(ls: TurtlePrefLanguageServer, params: types.InlayHintParams):
    idx = ls._documents.get(params.text_document.uri)
    if idx is None:
        return None

    start_line, end_line = params.range.start.line, params.range.end.line
    hints: List[types.InlayHint] = []

    for line in range(start_line, end_line + 1):
        for start, end, iri in idx.ranges.get(line, []):
            label = idx.labels.get(iri)
            if not label:
                continue
            hints.append(
                types.InlayHint(
                    position=types.Position(line=line, character=end),
                    label=label,
                    padding_left=True,
                )
            )
    return hints

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def start_io() -> None:
    """Run the language server on stdio (for Neovim)."""
    ls.start_io(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    start_io()
