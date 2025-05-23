"""ttl_pref_ls.server
~~~~~~~~~~~~~~~~~~~~~
Turtle‑aware language server that provides
* hover with `skos:prefLabel`
* Hint diagnostics for resources missing a label (now **all occurrences**)
* inline inlay‑hints (virtual text) showing the prefLabel next to each IRI/QName

It re‑uses the **indexer** for all semantic & lexical data.
"""
from __future__ import annotations

import logging
import sys
from typing import Dict, List

from pygls.server import LanguageServer
from rdflib.namespace import RDF
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
# Small util: pretty‑print an absolute IRI as QName if a prefix matches
# ---------------------------------------------------------------------------

def _pretty_iri(idx: DocumentIndex, iri: str) -> str:
    for pref, ns in idx.prefixes.items():
        if iri.startswith(ns):
            return f"{pref}:{iri[len(ns):]}"
    return f"<{iri}>"  # fallback

# ---------------------------------------------------------------------------
# Language‑server class
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
        text_document_sync=types.TextDocumentSyncOptions(
            open_close=True,
            change=types.TextDocumentSyncKind.Full,  # client will send whole text
            save=None,
        ),
    )
    server_info = types.InitializeResultServerInfoType(name=ls.name, version=ls.version)
    return types.InitializeResult(capabilities=capabilities, server_info=server_info)

# ---------------------------------------------------------------------------
# Document lifecycle helpers
# ---------------------------------------------------------------------------

def _index_and_store(ls: TurtlePrefLanguageServer, uri: str, text: str):
    """(Re)build the index; swallow parser errors so edits never crash LSP."""
    try:
        idx = build_index(text)
    except Exception as exc:
        log.debug("parse error ignored: %s", exc)
        return  # keep previous diagnostics until text is syntactically valid

    ls._documents[uri] = idx
    log.info("indexed %d URIs / %d prefLabels (%s)", len(idx.uris), len(idx.labels), uri)
    _publish_diagnostics(ls, uri, idx)
    idx = build_index(text)
    ls._documents[uri] = idx
    log.info("indexed %d URIs / %d prefLabels (%s)", len(idx.uris), len(idx.labels), uri)
    _publish_diagnostics(ls, uri, idx)

# didOpen --------------------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: TurtlePrefLanguageServer, params: types.DidOpenTextDocumentParams):
    _index_and_store(ls, params.text_document.uri, params.text_document.text)

# didChange (full‑text) -------------------------------------------------------

@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: TurtlePrefLanguageServer, params: types.DidChangeTextDocumentParams):
    """Handle incremental changes – always reparse the *current* buffer.

    Neovim sends Type1 (range‑based) edits even when the server advertises
    *Full* sync, but `pygls` automatically applies them to its internal
    `Document` object.  So we simply read the up‑to‑date source from the
    workspace cache and re‑index that.
    """
    try:
        full_text = ls.workspace.get_document(params.text_document.uri).source
    except Exception:
        return  # no document cache yet

    _index_and_store(ls, params.text_document.uri, full_text)


    # With TextDocumentSyncKind.Full we always get the entire file content in
    # the *last* change entry.
    full_text = params.content_changes[-1].text

    # If the client still sent an incremental patch (e.g. user config override),
    # fall back to the full text held by pygls' workspace cache.
    if full_text == "":
        try:
            full_text = ls.workspace.get_document(params.text_document.uri).source
        except Exception:
            return  # cannot recover – skip re‑index until next full change

    _index_and_store(ls, params.text_document.uri, full_text)


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

    # ------------------------------------------------------------
    # Special case: cursor on literal token "a" (rdf:type)
    # ------------------------------------------------------------
    if iri is None:
        try:
            line_txt = ls.workspace.get_document(params.text_document.uri).lines[pos.line]
        except Exception:
            line_txt = ""
        col = pos.character
        # crude word extraction
        start = col
        end = col
        while start > 0 and line_txt[start-1].isalnum():
            start -= 1
        while end < len(line_txt) and line_txt[end].isalnum():
            end += 1
        token = line_txt[start:end]
        if token == "a":
            iri = RDF.type
            # fabricate a range so Neovim shows hover even when idx misses it
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
    full_uri = f"<{str(iri)}>"

    if label:
        md = f"""{full_uri}
`{display}`
**prefLabel:** {label}"""
    else:
        md = f"""{full_uri}
`{display}`"""

    return types.Hover(
        contents=types.MarkupContent(kind="markdown", value=md),
        range=range_override,
    )

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
# INLAY‑HINTS
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

