"""Unit tests for ttl_pref_ls.resolver."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Dict

import pytest

from ttl_pref_ls import resolver

SAMPLE_TTL = """
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
<http://example.com/ns#Foo> skos:prefLabel "Foo"@en .
<http://example.com/ns#Bar> skos:prefLabel "Bar"@en .
"""


class _FakeResp:
    def __init__(self, status: int = 200, text: str = SAMPLE_TTL):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, status: int = 200, text: str = SAMPLE_TTL):
        self._status = status
        self._text = text
        self.last_url: str | None = None
        self.last_hdrs: Dict[str, str] | None = None

    # In real aiohttp, `get` is sync and returns an *async* context manager.
    def get(self, url, *, headers=None, timeout=None):
        self.last_url = url
        self.last_hdrs = headers
        return _FakeResp(self._status, self._text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_fetch_labels(monkeypatch):
    """_fetch_labels should parse remote Turtle and return prefLabel map."""

    def _fake_session_ctor(*args, **kwargs):
        return _FakeSession()

    # Monkey‑patch aiohttp module in resolver
    monkeypatch.setattr(
        resolver,
        "aiohttp",
        SimpleNamespace(ClientSession=_fake_session_ctor),
    )

    labels = await resolver._fetch_labels("http://example.com/ns")

    assert labels["http://example.com/ns#Foo"] == "Foo"

# Live network test (optional) – uses real EMMO namespace on w3id.org
EMMO_IRI = "https://w3id.org/emmo#EMMO_36c79456_e29c_400d_8bd3_0eedddb82652"

@pytest.mark.asyncio
async def test_fetch_emmo_live():
    """Resolver retrieves the label from the live EMMO ontology."""
    try:
        labels = await resolver._fetch_labels("https://w3id.org/emmo")
    except Exception:
        pytest.skip("Network unreachable or slow – skipping live test")

    if not labels:
        pytest.skip("Remote fetch returned empty – possible network issue")

    assert labels.get(EMMO_IRI) == "Arrangement"
